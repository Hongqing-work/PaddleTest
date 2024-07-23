import os
os.environ['FLAGS_cinn_new_group_scheduler'] = '1'
os.environ['FLAGS_group_schedule_tiling_first'] = '1'
os.environ['FLAGS_enable_pir_api'] = '1'
os.environ['FLAGS_cinn_bucket_compile'] = '1'
import sys
import unittest
import numpy as np
from dataclasses import dataclass
import typing as t

@dataclass
class Stage:
    name: str
    env_vars: t.Dict[str, str]

cinn_stages = [
    Stage(
        name="dynamic_to_static",
        env_vars=dict(
            PADDLE_DEBUG_ENABLE_CINN=False,
            FLAGS_prim_all=False,
            FLAGS_prim_enable_dynamic=False,
        ),
    ),
    Stage(
        name="prim",
        env_vars=dict(
            PADDLE_DEBUG_ENABLE_CINN=False,
            FLAGS_prim_all=True,
            FLAGS_prim_enable_dynamic=True,
        ),
    ),
    Stage(
        name="infer_symbolic",
        env_vars=dict(
            PADDLE_DEBUG_ENABLE_CINN=False,
            FLAGS_prim_all=True,
            FLAGS_prim_enable_dynamic=True,
            FLAGS_use_cinn=False,
            FLAGS_check_infer_symbolic=True,
        ),
    ),
	Stage(
        name="frontend",
        env_vars=dict(
            PADDLE_DEBUG_ENABLE_CINN=True,
            FLAGS_prim_all=True,
            FLAGS_prim_enable_dynamic=True,
            FLAGS_use_cinn=True,
            FLAGS_check_infer_symbolic=False,
            FLAGS_enable_fusion_fallback=True,
        ), 
    ),
    Stage(
        name="backend",
        env_vars=dict(
            PADDLE_DEBUG_ENABLE_CINN=True,
            FLAGS_prim_all=True,
            FLAGS_prim_enable_dynamic=True,
            FLAGS_use_cinn=True,
            FLAGS_check_infer_symbolic=False,
            FLAGS_enable_fusion_fallback=False,
        ), 
    ),
]

def GetCinnStageByName(name):
    for stage in cinn_stages:
        if stage.name == name:
            return stage
    return None

def GetCurrentCinnStage():
    name = os.getenv('PADDLE_DEBUG_CINN_STAGE_NAME')
    if name is None:
        return None
    stage_names = [stage.name for stage in cinn_stages]
    assert name in stage_names, (
        f"PADDLE_DEBUG_CINN_STAGE_NAME should be in {stage_names}"
    )
    return GetCinnStageByName(name)

def GetPrevCinnStage(stage):
    for i in range(1, len(cinn_stages)):
        if stage is cinn_stages[i]:
            return cinn_stages[i - 1]
    return None

def IsCinnStageEnableDiff():
    value = os.getenv('PADDLE_DEBUG_CINN_STAGE_ENABLE_DIFF')
    enabled = value in {
        '1',
        'true',
        'True',
    }
    if enabled:
        assert GetCurrentCinnStage() is not None
    return enabled

def GetExitCodeAndStdErr(cmd, env):
    env = {
        k:v
        for k, v in env.items()
        if v is not None
    }
    import subprocess
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    return result.returncode, result.stderr

def GetStageExitCodeAndStdErr(stage):
    return GetExitCodeAndStdErr(
        [sys.executable, __file__],
        env=dict(
            PADDLE_DEBUG_CINN_STAGE_NAME=stage.name,
            PADDLE_DEBUG_CINN_STAGE_ENABLE_DIFF='0',
            PYTHONPATH=os.getenv('PYTHONPATH'),
            ATHENA_ENABLE_TRY_RUN="False",
        ),
    )

def AthenaTryRunEnabled():
    return os.getenv('ATHENA_ENABLE_TRY_RUN') not in {
        "0",
        "False",
        "false",
        "OFF"
    }

def GetNeedSkipAndSkipMessage():
    current_stage = GetCurrentCinnStage()
    assert current_stage is not None
    if not IsCinnStageEnableDiff():
        return False, ""
    last_stage = GetPrevCinnStage(current_stage)
    if last_stage is None:
        return False, ""
    exitcode, stderr = GetStageExitCodeAndStdErr(last_stage)
    if exitcode != 0:
        return True, f"last stage failed."
    return False, ""

def GetCurrentStageTryRunExitCodeAndStdErr():
    if not AthenaTryRunEnabled():
        return False, ""
    current_stage = GetCurrentCinnStage()
    assert current_stage is not None
    return GetStageExitCodeAndStdErr(current_stage)

def SetDefaultEnv(**env_var2value):
    for env_var, value in env_var2value.items():
        if os.getenv(env_var) is None:
            os.environ[env_var] = str(value)

SetDefaultEnv(
    PADDLE_DEBUG_CINN_STAGE_NAME="backend",
    PADDLE_DEBUG_CINN_STAGE_ENABLE_DIFF=False,
    PADDLE_DEBUG_ENABLE_CINN=True,
    FLAGS_enable_pir_api=True,
    FLAGS_prim_all=True,
    FLAGS_prim_enable_dynamic=True,
    FLAGS_use_cinn=False,
    FLAGS_check_infer_symbolic=False,
    FLAGS_enable_fusion_fallback=False,
)

need_skip, skip_message = GetNeedSkipAndSkipMessage()
try_run_exit_code, try_run_stderr = GetCurrentStageTryRunExitCodeAndStdErr()
class TestTryRun(unittest.TestCase):
    def test_panic(self):
        if not AthenaTryRunEnabled():
            return
        if try_run_exit_code == 0:
            # All unittest cases passed.
            return
        if try_run_exit_code > 0:
            # program failed but not panic.
            return
        # program panicked.
        kOutputLimit = 65536
        message = try_run_stderr[-kOutputLimit:]
        raise RuntimeError(f"panicked. last {kOutputLimit} characters of stderr: \n{message}")

import paddle

def SetEnvVar(env_var2value):
    for env_var, value in env_var2value.items():
        os.environ[env_var] = str(value)
    paddle.set_flags({
        env_var:value
        for env_var, value in env_var2value.items()
        if env_var.startswith('FLAGS_')
    })

if GetCurrentCinnStage() is not None:
    SetEnvVar(GetCurrentCinnStage().env_vars)

def NumOperationsInBlock(block_idx):
    return [210][block_idx] - 1 # number-of-ops-in-block

def GetPaddleDebugNumAllowedOps():
    try:
        return int(os.getenv('PADDLE_DEBUG_NUM_ALLOWED_OPS'))
    except:
        return None

paddle_debug_num_allowed_ops = GetPaddleDebugNumAllowedOps()


if type(paddle_debug_num_allowed_ops) is not int:
    def EarlyReturn(block_idx, op_idx):
        return False      
else:
    def EarlyReturn(block_idx, op_idx):
        return op_idx >= paddle_debug_num_allowed_ops

class BlockEntries:
    def builtin_module_837_0_0(self, constant_1, parameter_256, parameter_254, parameter_237, parameter_235, parameter_218, parameter_216, parameter_199, parameter_197, parameter_180, parameter_178, parameter_101, parameter_99, parameter_82, parameter_80, parameter_63, parameter_61, constant_0, parameter_0, parameter_4, parameter_1, parameter_3, parameter_2, parameter_5, parameter_9, parameter_6, parameter_8, parameter_7, parameter_10, parameter_14, parameter_11, parameter_13, parameter_12, parameter_15, parameter_19, parameter_16, parameter_18, parameter_17, parameter_20, parameter_24, parameter_21, parameter_23, parameter_22, parameter_25, parameter_29, parameter_26, parameter_28, parameter_27, parameter_30, parameter_34, parameter_31, parameter_33, parameter_32, parameter_35, parameter_39, parameter_36, parameter_38, parameter_37, parameter_40, parameter_44, parameter_41, parameter_43, parameter_42, parameter_45, parameter_49, parameter_46, parameter_48, parameter_47, parameter_50, parameter_54, parameter_51, parameter_53, parameter_52, parameter_55, parameter_59, parameter_56, parameter_58, parameter_57, parameter_60, parameter_62, parameter_64, parameter_68, parameter_65, parameter_67, parameter_66, parameter_69, parameter_73, parameter_70, parameter_72, parameter_71, parameter_74, parameter_78, parameter_75, parameter_77, parameter_76, parameter_79, parameter_81, parameter_83, parameter_87, parameter_84, parameter_86, parameter_85, parameter_88, parameter_92, parameter_89, parameter_91, parameter_90, parameter_93, parameter_97, parameter_94, parameter_96, parameter_95, parameter_98, parameter_100, parameter_102, parameter_106, parameter_103, parameter_105, parameter_104, parameter_107, parameter_111, parameter_108, parameter_110, parameter_109, parameter_112, parameter_116, parameter_113, parameter_115, parameter_114, parameter_117, parameter_121, parameter_118, parameter_120, parameter_119, parameter_122, parameter_126, parameter_123, parameter_125, parameter_124, parameter_127, parameter_131, parameter_128, parameter_130, parameter_129, parameter_132, parameter_136, parameter_133, parameter_135, parameter_134, parameter_137, parameter_141, parameter_138, parameter_140, parameter_139, parameter_142, parameter_146, parameter_143, parameter_145, parameter_144, parameter_147, parameter_151, parameter_148, parameter_150, parameter_149, parameter_152, parameter_156, parameter_153, parameter_155, parameter_154, parameter_157, parameter_161, parameter_158, parameter_160, parameter_159, parameter_162, parameter_166, parameter_163, parameter_165, parameter_164, parameter_167, parameter_171, parameter_168, parameter_170, parameter_169, parameter_172, parameter_176, parameter_173, parameter_175, parameter_174, parameter_177, parameter_179, parameter_181, parameter_185, parameter_182, parameter_184, parameter_183, parameter_186, parameter_190, parameter_187, parameter_189, parameter_188, parameter_191, parameter_195, parameter_192, parameter_194, parameter_193, parameter_196, parameter_198, parameter_200, parameter_204, parameter_201, parameter_203, parameter_202, parameter_205, parameter_209, parameter_206, parameter_208, parameter_207, parameter_210, parameter_214, parameter_211, parameter_213, parameter_212, parameter_215, parameter_217, parameter_219, parameter_223, parameter_220, parameter_222, parameter_221, parameter_224, parameter_228, parameter_225, parameter_227, parameter_226, parameter_229, parameter_233, parameter_230, parameter_232, parameter_231, parameter_234, parameter_236, parameter_238, parameter_242, parameter_239, parameter_241, parameter_240, parameter_243, parameter_247, parameter_244, parameter_246, parameter_245, parameter_248, parameter_252, parameter_249, parameter_251, parameter_250, parameter_253, parameter_255, parameter_257, parameter_261, parameter_258, parameter_260, parameter_259, parameter_262, parameter_266, parameter_263, parameter_265, parameter_264, parameter_267, parameter_268, parameter_269, feed_0):

        # pd_op.cast: (-1x3x224x224xf16) <- (-1x3x224x224xf32)
        cast_0 = paddle._C_ops.cast(feed_0, paddle.float16)

        # pd_op.conv2d: (-1x8x112x112xf16) <- (-1x3x224x224xf16, 8x3x3x3xf16)
        conv2d_0 = paddle._C_ops.conv2d(cast_0, parameter_0, [2, 2], [1, 1], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x8x112x112xf16, 8xf32, 8xf32, 8xf32, 8xf32, None) <- (-1x8x112x112xf16, 8xf32, 8xf32, 8xf32, 8xf32)
        batch_norm__0, batch_norm__1, batch_norm__2, batch_norm__3, batch_norm__4, batch_norm__5 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_0, parameter_1, parameter_2, parameter_3, parameter_4, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x8x112x112xf16) <- (-1x8x112x112xf16)
        hardswish_0 = paddle._C_ops.hardswish(batch_norm__0)

        # pd_op.conv2d: (-1x8x112x112xf16) <- (-1x8x112x112xf16, 8x8x1x1xf16)
        conv2d_1 = paddle._C_ops.conv2d(hardswish_0, parameter_5, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x8x112x112xf16, 8xf32, 8xf32, 8xf32, 8xf32, None) <- (-1x8x112x112xf16, 8xf32, 8xf32, 8xf32, 8xf32)
        batch_norm__6, batch_norm__7, batch_norm__8, batch_norm__9, batch_norm__10, batch_norm__11 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_1, parameter_6, parameter_7, parameter_8, parameter_9, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x8x112x112xf16) <- (-1x8x112x112xf16)
        relu__0 = paddle._C_ops.relu_(batch_norm__6)

        # pd_op.depthwise_conv2d: (-1x8x112x112xf16) <- (-1x8x112x112xf16, 8x1x3x3xf16)
        depthwise_conv2d_0 = paddle._C_ops.depthwise_conv2d(relu__0, parameter_10, [1, 1], [1, 1], 'EXPLICIT', 8, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x8x112x112xf16, 8xf32, 8xf32, 8xf32, 8xf32, None) <- (-1x8x112x112xf16, 8xf32, 8xf32, 8xf32, 8xf32)
        batch_norm__12, batch_norm__13, batch_norm__14, batch_norm__15, batch_norm__16, batch_norm__17 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_0, parameter_11, parameter_12, parameter_13, parameter_14, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x8x112x112xf16) <- (-1x8x112x112xf16)
        relu__1 = paddle._C_ops.relu_(batch_norm__12)

        # pd_op.conv2d: (-1x8x112x112xf16) <- (-1x8x112x112xf16, 8x8x1x1xf16)
        conv2d_2 = paddle._C_ops.conv2d(relu__1, parameter_15, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x8x112x112xf16, 8xf32, 8xf32, 8xf32, 8xf32, None) <- (-1x8x112x112xf16, 8xf32, 8xf32, 8xf32, 8xf32)
        batch_norm__18, batch_norm__19, batch_norm__20, batch_norm__21, batch_norm__22, batch_norm__23 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_2, parameter_16, parameter_17, parameter_18, parameter_19, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add_: (-1x8x112x112xf16) <- (-1x8x112x112xf16, -1x8x112x112xf16)
        add__0 = paddle._C_ops.add_(hardswish_0, batch_norm__18)

        # pd_op.conv2d: (-1x24x112x112xf16) <- (-1x8x112x112xf16, 24x8x1x1xf16)
        conv2d_3 = paddle._C_ops.conv2d(add__0, parameter_20, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x112x112xf16, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x112x112xf16, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__24, batch_norm__25, batch_norm__26, batch_norm__27, batch_norm__28, batch_norm__29 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_3, parameter_21, parameter_22, parameter_23, parameter_24, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x112x112xf16) <- (-1x24x112x112xf16)
        relu__2 = paddle._C_ops.relu_(batch_norm__24)

        # pd_op.depthwise_conv2d: (-1x24x56x56xf16) <- (-1x24x112x112xf16, 24x1x3x3xf16)
        depthwise_conv2d_1 = paddle._C_ops.depthwise_conv2d(relu__2, parameter_25, [2, 2], [1, 1], 'EXPLICIT', 24, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x24x56x56xf16, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x56x56xf16, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__30, batch_norm__31, batch_norm__32, batch_norm__33, batch_norm__34, batch_norm__35 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_1, parameter_26, parameter_27, parameter_28, parameter_29, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x56x56xf16) <- (-1x24x56x56xf16)
        relu__3 = paddle._C_ops.relu_(batch_norm__30)

        # pd_op.conv2d: (-1x8x56x56xf16) <- (-1x24x56x56xf16, 8x24x1x1xf16)
        conv2d_4 = paddle._C_ops.conv2d(relu__3, parameter_30, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x8x56x56xf16, 8xf32, 8xf32, 8xf32, 8xf32, None) <- (-1x8x56x56xf16, 8xf32, 8xf32, 8xf32, 8xf32)
        batch_norm__36, batch_norm__37, batch_norm__38, batch_norm__39, batch_norm__40, batch_norm__41 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_4, parameter_31, parameter_32, parameter_33, parameter_34, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x24x56x56xf16) <- (-1x8x56x56xf16, 24x8x1x1xf16)
        conv2d_5 = paddle._C_ops.conv2d(batch_norm__36, parameter_35, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x56x56xf16, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x56x56xf16, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__42, batch_norm__43, batch_norm__44, batch_norm__45, batch_norm__46, batch_norm__47 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_5, parameter_36, parameter_37, parameter_38, parameter_39, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x56x56xf16) <- (-1x24x56x56xf16)
        relu__4 = paddle._C_ops.relu_(batch_norm__42)

        # pd_op.depthwise_conv2d: (-1x24x56x56xf16) <- (-1x24x56x56xf16, 24x1x3x3xf16)
        depthwise_conv2d_2 = paddle._C_ops.depthwise_conv2d(relu__4, parameter_40, [1, 1], [1, 1], 'EXPLICIT', 24, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x24x56x56xf16, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x56x56xf16, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__48, batch_norm__49, batch_norm__50, batch_norm__51, batch_norm__52, batch_norm__53 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_2, parameter_41, parameter_42, parameter_43, parameter_44, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x56x56xf16) <- (-1x24x56x56xf16)
        relu__5 = paddle._C_ops.relu_(batch_norm__48)

        # pd_op.conv2d: (-1x8x56x56xf16) <- (-1x24x56x56xf16, 8x24x1x1xf16)
        conv2d_6 = paddle._C_ops.conv2d(relu__5, parameter_45, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x8x56x56xf16, 8xf32, 8xf32, 8xf32, 8xf32, None) <- (-1x8x56x56xf16, 8xf32, 8xf32, 8xf32, 8xf32)
        batch_norm__54, batch_norm__55, batch_norm__56, batch_norm__57, batch_norm__58, batch_norm__59 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_6, parameter_46, parameter_47, parameter_48, parameter_49, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add_: (-1x8x56x56xf16) <- (-1x8x56x56xf16, -1x8x56x56xf16)
        add__1 = paddle._C_ops.add_(batch_norm__36, batch_norm__54)

        # pd_op.conv2d: (-1x24x56x56xf16) <- (-1x8x56x56xf16, 24x8x1x1xf16)
        conv2d_7 = paddle._C_ops.conv2d(add__1, parameter_50, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x24x56x56xf16, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x56x56xf16, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__60, batch_norm__61, batch_norm__62, batch_norm__63, batch_norm__64, batch_norm__65 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_7, parameter_51, parameter_52, parameter_53, parameter_54, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x56x56xf16) <- (-1x24x56x56xf16)
        relu__6 = paddle._C_ops.relu_(batch_norm__60)

        # pd_op.depthwise_conv2d: (-1x24x28x28xf16) <- (-1x24x56x56xf16, 24x1x5x5xf16)
        depthwise_conv2d_3 = paddle._C_ops.depthwise_conv2d(relu__6, parameter_55, [2, 2], [2, 2], 'EXPLICIT', 24, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x24x28x28xf16, 24xf32, 24xf32, 24xf32, 24xf32, None) <- (-1x24x28x28xf16, 24xf32, 24xf32, 24xf32, 24xf32)
        batch_norm__66, batch_norm__67, batch_norm__68, batch_norm__69, batch_norm__70, batch_norm__71 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_3, parameter_56, parameter_57, parameter_58, parameter_59, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x24x28x28xf16) <- (-1x24x28x28xf16)
        relu__7 = paddle._C_ops.relu_(batch_norm__66)

        # pd_op.pool2d: (-1x24x1x1xf16) <- (-1x24x28x28xf16, 2xi64)
        pool2d_0 = paddle._C_ops.pool2d(relu__7, constant_0, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x6x1x1xf16) <- (-1x24x1x1xf16, 6x24x1x1xf16)
        conv2d_8 = paddle._C_ops.conv2d(pool2d_0, parameter_60, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.add_: (-1x6x1x1xf16) <- (-1x6x1x1xf16, 1x6x1x1xf16)
        add__2 = paddle._C_ops.add_(conv2d_8, parameter_61)

        # pd_op.relu_: (-1x6x1x1xf16) <- (-1x6x1x1xf16)
        relu__8 = paddle._C_ops.relu_(add__2)

        # pd_op.conv2d: (-1x24x1x1xf16) <- (-1x6x1x1xf16, 24x6x1x1xf16)
        conv2d_9 = paddle._C_ops.conv2d(relu__8, parameter_62, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.add_: (-1x24x1x1xf16) <- (-1x24x1x1xf16, 1x24x1x1xf16)
        add__3 = paddle._C_ops.add_(conv2d_9, parameter_63)

        # pd_op.hardsigmoid: (-1x24x1x1xf16) <- (-1x24x1x1xf16)
        hardsigmoid_0 = paddle._C_ops.hardsigmoid(add__3, float('0.2'), float('0.5'))

        # pd_op.multiply_: (-1x24x28x28xf16) <- (-1x24x28x28xf16, -1x24x1x1xf16)
        multiply__0 = paddle._C_ops.multiply_(relu__7, hardsigmoid_0)

        # pd_op.conv2d: (-1x16x28x28xf16) <- (-1x24x28x28xf16, 16x24x1x1xf16)
        conv2d_10 = paddle._C_ops.conv2d(multiply__0, parameter_64, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x16x28x28xf16, 16xf32, 16xf32, 16xf32, 16xf32, None) <- (-1x16x28x28xf16, 16xf32, 16xf32, 16xf32, 16xf32)
        batch_norm__72, batch_norm__73, batch_norm__74, batch_norm__75, batch_norm__76, batch_norm__77 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_10, parameter_65, parameter_66, parameter_67, parameter_68, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x40x28x28xf16) <- (-1x16x28x28xf16, 40x16x1x1xf16)
        conv2d_11 = paddle._C_ops.conv2d(batch_norm__72, parameter_69, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x40x28x28xf16, 40xf32, 40xf32, 40xf32, 40xf32, None) <- (-1x40x28x28xf16, 40xf32, 40xf32, 40xf32, 40xf32)
        batch_norm__78, batch_norm__79, batch_norm__80, batch_norm__81, batch_norm__82, batch_norm__83 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_11, parameter_70, parameter_71, parameter_72, parameter_73, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x40x28x28xf16) <- (-1x40x28x28xf16)
        relu__9 = paddle._C_ops.relu_(batch_norm__78)

        # pd_op.depthwise_conv2d: (-1x40x28x28xf16) <- (-1x40x28x28xf16, 40x1x5x5xf16)
        depthwise_conv2d_4 = paddle._C_ops.depthwise_conv2d(relu__9, parameter_74, [1, 1], [2, 2], 'EXPLICIT', 40, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x40x28x28xf16, 40xf32, 40xf32, 40xf32, 40xf32, None) <- (-1x40x28x28xf16, 40xf32, 40xf32, 40xf32, 40xf32)
        batch_norm__84, batch_norm__85, batch_norm__86, batch_norm__87, batch_norm__88, batch_norm__89 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_4, parameter_75, parameter_76, parameter_77, parameter_78, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x40x28x28xf16) <- (-1x40x28x28xf16)
        relu__10 = paddle._C_ops.relu_(batch_norm__84)

        # pd_op.pool2d: (-1x40x1x1xf16) <- (-1x40x28x28xf16, 2xi64)
        pool2d_1 = paddle._C_ops.pool2d(relu__10, constant_0, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x10x1x1xf16) <- (-1x40x1x1xf16, 10x40x1x1xf16)
        conv2d_12 = paddle._C_ops.conv2d(pool2d_1, parameter_79, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.add_: (-1x10x1x1xf16) <- (-1x10x1x1xf16, 1x10x1x1xf16)
        add__4 = paddle._C_ops.add_(conv2d_12, parameter_80)

        # pd_op.relu_: (-1x10x1x1xf16) <- (-1x10x1x1xf16)
        relu__11 = paddle._C_ops.relu_(add__4)

        # pd_op.conv2d: (-1x40x1x1xf16) <- (-1x10x1x1xf16, 40x10x1x1xf16)
        conv2d_13 = paddle._C_ops.conv2d(relu__11, parameter_81, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.add_: (-1x40x1x1xf16) <- (-1x40x1x1xf16, 1x40x1x1xf16)
        add__5 = paddle._C_ops.add_(conv2d_13, parameter_82)

        # pd_op.hardsigmoid: (-1x40x1x1xf16) <- (-1x40x1x1xf16)
        hardsigmoid_1 = paddle._C_ops.hardsigmoid(add__5, float('0.2'), float('0.5'))

        # pd_op.multiply_: (-1x40x28x28xf16) <- (-1x40x28x28xf16, -1x40x1x1xf16)
        multiply__1 = paddle._C_ops.multiply_(relu__10, hardsigmoid_1)

        # pd_op.conv2d: (-1x16x28x28xf16) <- (-1x40x28x28xf16, 16x40x1x1xf16)
        conv2d_14 = paddle._C_ops.conv2d(multiply__1, parameter_83, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x16x28x28xf16, 16xf32, 16xf32, 16xf32, 16xf32, None) <- (-1x16x28x28xf16, 16xf32, 16xf32, 16xf32, 16xf32)
        batch_norm__90, batch_norm__91, batch_norm__92, batch_norm__93, batch_norm__94, batch_norm__95 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_14, parameter_84, parameter_85, parameter_86, parameter_87, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add_: (-1x16x28x28xf16) <- (-1x16x28x28xf16, -1x16x28x28xf16)
        add__6 = paddle._C_ops.add_(batch_norm__72, batch_norm__90)

        # pd_op.conv2d: (-1x40x28x28xf16) <- (-1x16x28x28xf16, 40x16x1x1xf16)
        conv2d_15 = paddle._C_ops.conv2d(add__6, parameter_88, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x40x28x28xf16, 40xf32, 40xf32, 40xf32, 40xf32, None) <- (-1x40x28x28xf16, 40xf32, 40xf32, 40xf32, 40xf32)
        batch_norm__96, batch_norm__97, batch_norm__98, batch_norm__99, batch_norm__100, batch_norm__101 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_15, parameter_89, parameter_90, parameter_91, parameter_92, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x40x28x28xf16) <- (-1x40x28x28xf16)
        relu__12 = paddle._C_ops.relu_(batch_norm__96)

        # pd_op.depthwise_conv2d: (-1x40x28x28xf16) <- (-1x40x28x28xf16, 40x1x5x5xf16)
        depthwise_conv2d_5 = paddle._C_ops.depthwise_conv2d(relu__12, parameter_93, [1, 1], [2, 2], 'EXPLICIT', 40, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x40x28x28xf16, 40xf32, 40xf32, 40xf32, 40xf32, None) <- (-1x40x28x28xf16, 40xf32, 40xf32, 40xf32, 40xf32)
        batch_norm__102, batch_norm__103, batch_norm__104, batch_norm__105, batch_norm__106, batch_norm__107 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_5, parameter_94, parameter_95, parameter_96, parameter_97, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.relu_: (-1x40x28x28xf16) <- (-1x40x28x28xf16)
        relu__13 = paddle._C_ops.relu_(batch_norm__102)

        # pd_op.pool2d: (-1x40x1x1xf16) <- (-1x40x28x28xf16, 2xi64)
        pool2d_2 = paddle._C_ops.pool2d(relu__13, constant_0, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x10x1x1xf16) <- (-1x40x1x1xf16, 10x40x1x1xf16)
        conv2d_16 = paddle._C_ops.conv2d(pool2d_2, parameter_98, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.add_: (-1x10x1x1xf16) <- (-1x10x1x1xf16, 1x10x1x1xf16)
        add__7 = paddle._C_ops.add_(conv2d_16, parameter_99)

        # pd_op.relu_: (-1x10x1x1xf16) <- (-1x10x1x1xf16)
        relu__14 = paddle._C_ops.relu_(add__7)

        # pd_op.conv2d: (-1x40x1x1xf16) <- (-1x10x1x1xf16, 40x10x1x1xf16)
        conv2d_17 = paddle._C_ops.conv2d(relu__14, parameter_100, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.add_: (-1x40x1x1xf16) <- (-1x40x1x1xf16, 1x40x1x1xf16)
        add__8 = paddle._C_ops.add_(conv2d_17, parameter_101)

        # pd_op.hardsigmoid: (-1x40x1x1xf16) <- (-1x40x1x1xf16)
        hardsigmoid_2 = paddle._C_ops.hardsigmoid(add__8, float('0.2'), float('0.5'))

        # pd_op.multiply_: (-1x40x28x28xf16) <- (-1x40x28x28xf16, -1x40x1x1xf16)
        multiply__2 = paddle._C_ops.multiply_(relu__13, hardsigmoid_2)

        # pd_op.conv2d: (-1x16x28x28xf16) <- (-1x40x28x28xf16, 16x40x1x1xf16)
        conv2d_18 = paddle._C_ops.conv2d(multiply__2, parameter_102, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x16x28x28xf16, 16xf32, 16xf32, 16xf32, 16xf32, None) <- (-1x16x28x28xf16, 16xf32, 16xf32, 16xf32, 16xf32)
        batch_norm__108, batch_norm__109, batch_norm__110, batch_norm__111, batch_norm__112, batch_norm__113 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_18, parameter_103, parameter_104, parameter_105, parameter_106, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add_: (-1x16x28x28xf16) <- (-1x16x28x28xf16, -1x16x28x28xf16)
        add__9 = paddle._C_ops.add_(add__6, batch_norm__108)

        # pd_op.conv2d: (-1x88x28x28xf16) <- (-1x16x28x28xf16, 88x16x1x1xf16)
        conv2d_19 = paddle._C_ops.conv2d(add__9, parameter_107, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x88x28x28xf16, 88xf32, 88xf32, 88xf32, 88xf32, None) <- (-1x88x28x28xf16, 88xf32, 88xf32, 88xf32, 88xf32)
        batch_norm__114, batch_norm__115, batch_norm__116, batch_norm__117, batch_norm__118, batch_norm__119 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_19, parameter_108, parameter_109, parameter_110, parameter_111, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x88x28x28xf16) <- (-1x88x28x28xf16)
        hardswish_1 = paddle._C_ops.hardswish(batch_norm__114)

        # pd_op.depthwise_conv2d: (-1x88x14x14xf16) <- (-1x88x28x28xf16, 88x1x3x3xf16)
        depthwise_conv2d_6 = paddle._C_ops.depthwise_conv2d(hardswish_1, parameter_112, [2, 2], [1, 1], 'EXPLICIT', 88, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x88x14x14xf16, 88xf32, 88xf32, 88xf32, 88xf32, None) <- (-1x88x14x14xf16, 88xf32, 88xf32, 88xf32, 88xf32)
        batch_norm__120, batch_norm__121, batch_norm__122, batch_norm__123, batch_norm__124, batch_norm__125 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_6, parameter_113, parameter_114, parameter_115, parameter_116, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x88x14x14xf16) <- (-1x88x14x14xf16)
        hardswish_2 = paddle._C_ops.hardswish(batch_norm__120)

        # pd_op.conv2d: (-1x32x14x14xf16) <- (-1x88x14x14xf16, 32x88x1x1xf16)
        conv2d_20 = paddle._C_ops.conv2d(hardswish_2, parameter_117, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x32x14x14xf16, 32xf32, 32xf32, 32xf32, 32xf32, None) <- (-1x32x14x14xf16, 32xf32, 32xf32, 32xf32, 32xf32)
        batch_norm__126, batch_norm__127, batch_norm__128, batch_norm__129, batch_norm__130, batch_norm__131 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_20, parameter_118, parameter_119, parameter_120, parameter_121, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x72x14x14xf16) <- (-1x32x14x14xf16, 72x32x1x1xf16)
        conv2d_21 = paddle._C_ops.conv2d(batch_norm__126, parameter_122, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x72x14x14xf16, 72xf32, 72xf32, 72xf32, 72xf32, None) <- (-1x72x14x14xf16, 72xf32, 72xf32, 72xf32, 72xf32)
        batch_norm__132, batch_norm__133, batch_norm__134, batch_norm__135, batch_norm__136, batch_norm__137 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_21, parameter_123, parameter_124, parameter_125, parameter_126, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x72x14x14xf16) <- (-1x72x14x14xf16)
        hardswish_3 = paddle._C_ops.hardswish(batch_norm__132)

        # pd_op.depthwise_conv2d: (-1x72x14x14xf16) <- (-1x72x14x14xf16, 72x1x3x3xf16)
        depthwise_conv2d_7 = paddle._C_ops.depthwise_conv2d(hardswish_3, parameter_127, [1, 1], [1, 1], 'EXPLICIT', 72, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x72x14x14xf16, 72xf32, 72xf32, 72xf32, 72xf32, None) <- (-1x72x14x14xf16, 72xf32, 72xf32, 72xf32, 72xf32)
        batch_norm__138, batch_norm__139, batch_norm__140, batch_norm__141, batch_norm__142, batch_norm__143 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_7, parameter_128, parameter_129, parameter_130, parameter_131, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x72x14x14xf16) <- (-1x72x14x14xf16)
        hardswish_4 = paddle._C_ops.hardswish(batch_norm__138)

        # pd_op.conv2d: (-1x32x14x14xf16) <- (-1x72x14x14xf16, 32x72x1x1xf16)
        conv2d_22 = paddle._C_ops.conv2d(hardswish_4, parameter_132, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x32x14x14xf16, 32xf32, 32xf32, 32xf32, 32xf32, None) <- (-1x32x14x14xf16, 32xf32, 32xf32, 32xf32, 32xf32)
        batch_norm__144, batch_norm__145, batch_norm__146, batch_norm__147, batch_norm__148, batch_norm__149 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_22, parameter_133, parameter_134, parameter_135, parameter_136, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add_: (-1x32x14x14xf16) <- (-1x32x14x14xf16, -1x32x14x14xf16)
        add__10 = paddle._C_ops.add_(batch_norm__126, batch_norm__144)

        # pd_op.conv2d: (-1x64x14x14xf16) <- (-1x32x14x14xf16, 64x32x1x1xf16)
        conv2d_23 = paddle._C_ops.conv2d(add__10, parameter_137, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x64x14x14xf16, 64xf32, 64xf32, 64xf32, 64xf32, None) <- (-1x64x14x14xf16, 64xf32, 64xf32, 64xf32, 64xf32)
        batch_norm__150, batch_norm__151, batch_norm__152, batch_norm__153, batch_norm__154, batch_norm__155 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_23, parameter_138, parameter_139, parameter_140, parameter_141, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x64x14x14xf16) <- (-1x64x14x14xf16)
        hardswish_5 = paddle._C_ops.hardswish(batch_norm__150)

        # pd_op.depthwise_conv2d: (-1x64x14x14xf16) <- (-1x64x14x14xf16, 64x1x3x3xf16)
        depthwise_conv2d_8 = paddle._C_ops.depthwise_conv2d(hardswish_5, parameter_142, [1, 1], [1, 1], 'EXPLICIT', 64, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x64x14x14xf16, 64xf32, 64xf32, 64xf32, 64xf32, None) <- (-1x64x14x14xf16, 64xf32, 64xf32, 64xf32, 64xf32)
        batch_norm__156, batch_norm__157, batch_norm__158, batch_norm__159, batch_norm__160, batch_norm__161 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_8, parameter_143, parameter_144, parameter_145, parameter_146, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x64x14x14xf16) <- (-1x64x14x14xf16)
        hardswish_6 = paddle._C_ops.hardswish(batch_norm__156)

        # pd_op.conv2d: (-1x32x14x14xf16) <- (-1x64x14x14xf16, 32x64x1x1xf16)
        conv2d_24 = paddle._C_ops.conv2d(hardswish_6, parameter_147, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x32x14x14xf16, 32xf32, 32xf32, 32xf32, 32xf32, None) <- (-1x32x14x14xf16, 32xf32, 32xf32, 32xf32, 32xf32)
        batch_norm__162, batch_norm__163, batch_norm__164, batch_norm__165, batch_norm__166, batch_norm__167 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_24, parameter_148, parameter_149, parameter_150, parameter_151, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add_: (-1x32x14x14xf16) <- (-1x32x14x14xf16, -1x32x14x14xf16)
        add__11 = paddle._C_ops.add_(add__10, batch_norm__162)

        # pd_op.conv2d: (-1x64x14x14xf16) <- (-1x32x14x14xf16, 64x32x1x1xf16)
        conv2d_25 = paddle._C_ops.conv2d(add__11, parameter_152, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x64x14x14xf16, 64xf32, 64xf32, 64xf32, 64xf32, None) <- (-1x64x14x14xf16, 64xf32, 64xf32, 64xf32, 64xf32)
        batch_norm__168, batch_norm__169, batch_norm__170, batch_norm__171, batch_norm__172, batch_norm__173 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_25, parameter_153, parameter_154, parameter_155, parameter_156, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x64x14x14xf16) <- (-1x64x14x14xf16)
        hardswish_7 = paddle._C_ops.hardswish(batch_norm__168)

        # pd_op.depthwise_conv2d: (-1x64x14x14xf16) <- (-1x64x14x14xf16, 64x1x3x3xf16)
        depthwise_conv2d_9 = paddle._C_ops.depthwise_conv2d(hardswish_7, parameter_157, [1, 1], [1, 1], 'EXPLICIT', 64, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x64x14x14xf16, 64xf32, 64xf32, 64xf32, 64xf32, None) <- (-1x64x14x14xf16, 64xf32, 64xf32, 64xf32, 64xf32)
        batch_norm__174, batch_norm__175, batch_norm__176, batch_norm__177, batch_norm__178, batch_norm__179 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_9, parameter_158, parameter_159, parameter_160, parameter_161, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x64x14x14xf16) <- (-1x64x14x14xf16)
        hardswish_8 = paddle._C_ops.hardswish(batch_norm__174)

        # pd_op.conv2d: (-1x32x14x14xf16) <- (-1x64x14x14xf16, 32x64x1x1xf16)
        conv2d_26 = paddle._C_ops.conv2d(hardswish_8, parameter_162, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x32x14x14xf16, 32xf32, 32xf32, 32xf32, 32xf32, None) <- (-1x32x14x14xf16, 32xf32, 32xf32, 32xf32, 32xf32)
        batch_norm__180, batch_norm__181, batch_norm__182, batch_norm__183, batch_norm__184, batch_norm__185 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_26, parameter_163, parameter_164, parameter_165, parameter_166, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add_: (-1x32x14x14xf16) <- (-1x32x14x14xf16, -1x32x14x14xf16)
        add__12 = paddle._C_ops.add_(add__11, batch_norm__180)

        # pd_op.conv2d: (-1x168x14x14xf16) <- (-1x32x14x14xf16, 168x32x1x1xf16)
        conv2d_27 = paddle._C_ops.conv2d(add__12, parameter_167, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x168x14x14xf16, 168xf32, 168xf32, 168xf32, 168xf32, None) <- (-1x168x14x14xf16, 168xf32, 168xf32, 168xf32, 168xf32)
        batch_norm__186, batch_norm__187, batch_norm__188, batch_norm__189, batch_norm__190, batch_norm__191 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_27, parameter_168, parameter_169, parameter_170, parameter_171, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x168x14x14xf16) <- (-1x168x14x14xf16)
        hardswish_9 = paddle._C_ops.hardswish(batch_norm__186)

        # pd_op.depthwise_conv2d: (-1x168x14x14xf16) <- (-1x168x14x14xf16, 168x1x3x3xf16)
        depthwise_conv2d_10 = paddle._C_ops.depthwise_conv2d(hardswish_9, parameter_172, [1, 1], [1, 1], 'EXPLICIT', 168, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x168x14x14xf16, 168xf32, 168xf32, 168xf32, 168xf32, None) <- (-1x168x14x14xf16, 168xf32, 168xf32, 168xf32, 168xf32)
        batch_norm__192, batch_norm__193, batch_norm__194, batch_norm__195, batch_norm__196, batch_norm__197 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_10, parameter_173, parameter_174, parameter_175, parameter_176, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x168x14x14xf16) <- (-1x168x14x14xf16)
        hardswish_10 = paddle._C_ops.hardswish(batch_norm__192)

        # pd_op.pool2d: (-1x168x1x1xf16) <- (-1x168x14x14xf16, 2xi64)
        pool2d_3 = paddle._C_ops.pool2d(hardswish_10, constant_0, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x42x1x1xf16) <- (-1x168x1x1xf16, 42x168x1x1xf16)
        conv2d_28 = paddle._C_ops.conv2d(pool2d_3, parameter_177, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.add_: (-1x42x1x1xf16) <- (-1x42x1x1xf16, 1x42x1x1xf16)
        add__13 = paddle._C_ops.add_(conv2d_28, parameter_178)

        # pd_op.relu_: (-1x42x1x1xf16) <- (-1x42x1x1xf16)
        relu__15 = paddle._C_ops.relu_(add__13)

        # pd_op.conv2d: (-1x168x1x1xf16) <- (-1x42x1x1xf16, 168x42x1x1xf16)
        conv2d_29 = paddle._C_ops.conv2d(relu__15, parameter_179, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.add_: (-1x168x1x1xf16) <- (-1x168x1x1xf16, 1x168x1x1xf16)
        add__14 = paddle._C_ops.add_(conv2d_29, parameter_180)

        # pd_op.hardsigmoid: (-1x168x1x1xf16) <- (-1x168x1x1xf16)
        hardsigmoid_3 = paddle._C_ops.hardsigmoid(add__14, float('0.2'), float('0.5'))

        # pd_op.multiply_: (-1x168x14x14xf16) <- (-1x168x14x14xf16, -1x168x1x1xf16)
        multiply__3 = paddle._C_ops.multiply_(hardswish_10, hardsigmoid_3)

        # pd_op.conv2d: (-1x40x14x14xf16) <- (-1x168x14x14xf16, 40x168x1x1xf16)
        conv2d_30 = paddle._C_ops.conv2d(multiply__3, parameter_181, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x40x14x14xf16, 40xf32, 40xf32, 40xf32, 40xf32, None) <- (-1x40x14x14xf16, 40xf32, 40xf32, 40xf32, 40xf32)
        batch_norm__198, batch_norm__199, batch_norm__200, batch_norm__201, batch_norm__202, batch_norm__203 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_30, parameter_182, parameter_183, parameter_184, parameter_185, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x232x14x14xf16) <- (-1x40x14x14xf16, 232x40x1x1xf16)
        conv2d_31 = paddle._C_ops.conv2d(batch_norm__198, parameter_186, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x232x14x14xf16, 232xf32, 232xf32, 232xf32, 232xf32, None) <- (-1x232x14x14xf16, 232xf32, 232xf32, 232xf32, 232xf32)
        batch_norm__204, batch_norm__205, batch_norm__206, batch_norm__207, batch_norm__208, batch_norm__209 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_31, parameter_187, parameter_188, parameter_189, parameter_190, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x232x14x14xf16) <- (-1x232x14x14xf16)
        hardswish_11 = paddle._C_ops.hardswish(batch_norm__204)

        # pd_op.depthwise_conv2d: (-1x232x14x14xf16) <- (-1x232x14x14xf16, 232x1x3x3xf16)
        depthwise_conv2d_11 = paddle._C_ops.depthwise_conv2d(hardswish_11, parameter_191, [1, 1], [1, 1], 'EXPLICIT', 232, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x232x14x14xf16, 232xf32, 232xf32, 232xf32, 232xf32, None) <- (-1x232x14x14xf16, 232xf32, 232xf32, 232xf32, 232xf32)
        batch_norm__210, batch_norm__211, batch_norm__212, batch_norm__213, batch_norm__214, batch_norm__215 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_11, parameter_192, parameter_193, parameter_194, parameter_195, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x232x14x14xf16) <- (-1x232x14x14xf16)
        hardswish_12 = paddle._C_ops.hardswish(batch_norm__210)

        # pd_op.pool2d: (-1x232x1x1xf16) <- (-1x232x14x14xf16, 2xi64)
        pool2d_4 = paddle._C_ops.pool2d(hardswish_12, constant_0, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x58x1x1xf16) <- (-1x232x1x1xf16, 58x232x1x1xf16)
        conv2d_32 = paddle._C_ops.conv2d(pool2d_4, parameter_196, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.add_: (-1x58x1x1xf16) <- (-1x58x1x1xf16, 1x58x1x1xf16)
        add__15 = paddle._C_ops.add_(conv2d_32, parameter_197)

        # pd_op.relu_: (-1x58x1x1xf16) <- (-1x58x1x1xf16)
        relu__16 = paddle._C_ops.relu_(add__15)

        # pd_op.conv2d: (-1x232x1x1xf16) <- (-1x58x1x1xf16, 232x58x1x1xf16)
        conv2d_33 = paddle._C_ops.conv2d(relu__16, parameter_198, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.add_: (-1x232x1x1xf16) <- (-1x232x1x1xf16, 1x232x1x1xf16)
        add__16 = paddle._C_ops.add_(conv2d_33, parameter_199)

        # pd_op.hardsigmoid: (-1x232x1x1xf16) <- (-1x232x1x1xf16)
        hardsigmoid_4 = paddle._C_ops.hardsigmoid(add__16, float('0.2'), float('0.5'))

        # pd_op.multiply_: (-1x232x14x14xf16) <- (-1x232x14x14xf16, -1x232x1x1xf16)
        multiply__4 = paddle._C_ops.multiply_(hardswish_12, hardsigmoid_4)

        # pd_op.conv2d: (-1x40x14x14xf16) <- (-1x232x14x14xf16, 40x232x1x1xf16)
        conv2d_34 = paddle._C_ops.conv2d(multiply__4, parameter_200, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x40x14x14xf16, 40xf32, 40xf32, 40xf32, 40xf32, None) <- (-1x40x14x14xf16, 40xf32, 40xf32, 40xf32, 40xf32)
        batch_norm__216, batch_norm__217, batch_norm__218, batch_norm__219, batch_norm__220, batch_norm__221 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_34, parameter_201, parameter_202, parameter_203, parameter_204, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add_: (-1x40x14x14xf16) <- (-1x40x14x14xf16, -1x40x14x14xf16)
        add__17 = paddle._C_ops.add_(batch_norm__198, batch_norm__216)

        # pd_op.conv2d: (-1x232x14x14xf16) <- (-1x40x14x14xf16, 232x40x1x1xf16)
        conv2d_35 = paddle._C_ops.conv2d(add__17, parameter_205, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x232x14x14xf16, 232xf32, 232xf32, 232xf32, 232xf32, None) <- (-1x232x14x14xf16, 232xf32, 232xf32, 232xf32, 232xf32)
        batch_norm__222, batch_norm__223, batch_norm__224, batch_norm__225, batch_norm__226, batch_norm__227 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_35, parameter_206, parameter_207, parameter_208, parameter_209, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x232x14x14xf16) <- (-1x232x14x14xf16)
        hardswish_13 = paddle._C_ops.hardswish(batch_norm__222)

        # pd_op.depthwise_conv2d: (-1x232x7x7xf16) <- (-1x232x14x14xf16, 232x1x5x5xf16)
        depthwise_conv2d_12 = paddle._C_ops.depthwise_conv2d(hardswish_13, parameter_210, [2, 2], [2, 2], 'EXPLICIT', 232, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x232x7x7xf16, 232xf32, 232xf32, 232xf32, 232xf32, None) <- (-1x232x7x7xf16, 232xf32, 232xf32, 232xf32, 232xf32)
        batch_norm__228, batch_norm__229, batch_norm__230, batch_norm__231, batch_norm__232, batch_norm__233 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_12, parameter_211, parameter_212, parameter_213, parameter_214, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x232x7x7xf16) <- (-1x232x7x7xf16)
        hardswish_14 = paddle._C_ops.hardswish(batch_norm__228)

        # pd_op.pool2d: (-1x232x1x1xf16) <- (-1x232x7x7xf16, 2xi64)
        pool2d_5 = paddle._C_ops.pool2d(hardswish_14, constant_0, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x58x1x1xf16) <- (-1x232x1x1xf16, 58x232x1x1xf16)
        conv2d_36 = paddle._C_ops.conv2d(pool2d_5, parameter_215, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.add_: (-1x58x1x1xf16) <- (-1x58x1x1xf16, 1x58x1x1xf16)
        add__18 = paddle._C_ops.add_(conv2d_36, parameter_216)

        # pd_op.relu_: (-1x58x1x1xf16) <- (-1x58x1x1xf16)
        relu__17 = paddle._C_ops.relu_(add__18)

        # pd_op.conv2d: (-1x232x1x1xf16) <- (-1x58x1x1xf16, 232x58x1x1xf16)
        conv2d_37 = paddle._C_ops.conv2d(relu__17, parameter_217, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.add_: (-1x232x1x1xf16) <- (-1x232x1x1xf16, 1x232x1x1xf16)
        add__19 = paddle._C_ops.add_(conv2d_37, parameter_218)

        # pd_op.hardsigmoid: (-1x232x1x1xf16) <- (-1x232x1x1xf16)
        hardsigmoid_5 = paddle._C_ops.hardsigmoid(add__19, float('0.2'), float('0.5'))

        # pd_op.multiply_: (-1x232x7x7xf16) <- (-1x232x7x7xf16, -1x232x1x1xf16)
        multiply__5 = paddle._C_ops.multiply_(hardswish_14, hardsigmoid_5)

        # pd_op.conv2d: (-1x56x7x7xf16) <- (-1x232x7x7xf16, 56x232x1x1xf16)
        conv2d_38 = paddle._C_ops.conv2d(multiply__5, parameter_219, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x56x7x7xf16, 56xf32, 56xf32, 56xf32, 56xf32, None) <- (-1x56x7x7xf16, 56xf32, 56xf32, 56xf32, 56xf32)
        batch_norm__234, batch_norm__235, batch_norm__236, batch_norm__237, batch_norm__238, batch_norm__239 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_38, parameter_220, parameter_221, parameter_222, parameter_223, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.conv2d: (-1x336x7x7xf16) <- (-1x56x7x7xf16, 336x56x1x1xf16)
        conv2d_39 = paddle._C_ops.conv2d(batch_norm__234, parameter_224, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x336x7x7xf16, 336xf32, 336xf32, 336xf32, 336xf32, None) <- (-1x336x7x7xf16, 336xf32, 336xf32, 336xf32, 336xf32)
        batch_norm__240, batch_norm__241, batch_norm__242, batch_norm__243, batch_norm__244, batch_norm__245 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_39, parameter_225, parameter_226, parameter_227, parameter_228, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x336x7x7xf16) <- (-1x336x7x7xf16)
        hardswish_15 = paddle._C_ops.hardswish(batch_norm__240)

        # pd_op.depthwise_conv2d: (-1x336x7x7xf16) <- (-1x336x7x7xf16, 336x1x5x5xf16)
        depthwise_conv2d_13 = paddle._C_ops.depthwise_conv2d(hardswish_15, parameter_229, [1, 1], [2, 2], 'EXPLICIT', 336, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x336x7x7xf16, 336xf32, 336xf32, 336xf32, 336xf32, None) <- (-1x336x7x7xf16, 336xf32, 336xf32, 336xf32, 336xf32)
        batch_norm__246, batch_norm__247, batch_norm__248, batch_norm__249, batch_norm__250, batch_norm__251 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_13, parameter_230, parameter_231, parameter_232, parameter_233, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x336x7x7xf16) <- (-1x336x7x7xf16)
        hardswish_16 = paddle._C_ops.hardswish(batch_norm__246)

        # pd_op.pool2d: (-1x336x1x1xf16) <- (-1x336x7x7xf16, 2xi64)
        pool2d_6 = paddle._C_ops.pool2d(hardswish_16, constant_0, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x84x1x1xf16) <- (-1x336x1x1xf16, 84x336x1x1xf16)
        conv2d_40 = paddle._C_ops.conv2d(pool2d_6, parameter_234, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.add_: (-1x84x1x1xf16) <- (-1x84x1x1xf16, 1x84x1x1xf16)
        add__20 = paddle._C_ops.add_(conv2d_40, parameter_235)

        # pd_op.relu_: (-1x84x1x1xf16) <- (-1x84x1x1xf16)
        relu__18 = paddle._C_ops.relu_(add__20)

        # pd_op.conv2d: (-1x336x1x1xf16) <- (-1x84x1x1xf16, 336x84x1x1xf16)
        conv2d_41 = paddle._C_ops.conv2d(relu__18, parameter_236, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.add_: (-1x336x1x1xf16) <- (-1x336x1x1xf16, 1x336x1x1xf16)
        add__21 = paddle._C_ops.add_(conv2d_41, parameter_237)

        # pd_op.hardsigmoid: (-1x336x1x1xf16) <- (-1x336x1x1xf16)
        hardsigmoid_6 = paddle._C_ops.hardsigmoid(add__21, float('0.2'), float('0.5'))

        # pd_op.multiply_: (-1x336x7x7xf16) <- (-1x336x7x7xf16, -1x336x1x1xf16)
        multiply__6 = paddle._C_ops.multiply_(hardswish_16, hardsigmoid_6)

        # pd_op.conv2d: (-1x56x7x7xf16) <- (-1x336x7x7xf16, 56x336x1x1xf16)
        conv2d_42 = paddle._C_ops.conv2d(multiply__6, parameter_238, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x56x7x7xf16, 56xf32, 56xf32, 56xf32, 56xf32, None) <- (-1x56x7x7xf16, 56xf32, 56xf32, 56xf32, 56xf32)
        batch_norm__252, batch_norm__253, batch_norm__254, batch_norm__255, batch_norm__256, batch_norm__257 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_42, parameter_239, parameter_240, parameter_241, parameter_242, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add_: (-1x56x7x7xf16) <- (-1x56x7x7xf16, -1x56x7x7xf16)
        add__22 = paddle._C_ops.add_(batch_norm__234, batch_norm__252)

        # pd_op.conv2d: (-1x336x7x7xf16) <- (-1x56x7x7xf16, 336x56x1x1xf16)
        conv2d_43 = paddle._C_ops.conv2d(add__22, parameter_243, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x336x7x7xf16, 336xf32, 336xf32, 336xf32, 336xf32, None) <- (-1x336x7x7xf16, 336xf32, 336xf32, 336xf32, 336xf32)
        batch_norm__258, batch_norm__259, batch_norm__260, batch_norm__261, batch_norm__262, batch_norm__263 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_43, parameter_244, parameter_245, parameter_246, parameter_247, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x336x7x7xf16) <- (-1x336x7x7xf16)
        hardswish_17 = paddle._C_ops.hardswish(batch_norm__258)

        # pd_op.depthwise_conv2d: (-1x336x7x7xf16) <- (-1x336x7x7xf16, 336x1x5x5xf16)
        depthwise_conv2d_14 = paddle._C_ops.depthwise_conv2d(hardswish_17, parameter_248, [1, 1], [2, 2], 'EXPLICIT', 336, [1, 1], 'NCHW')

        # pd_op.batch_norm_: (-1x336x7x7xf16, 336xf32, 336xf32, 336xf32, 336xf32, None) <- (-1x336x7x7xf16, 336xf32, 336xf32, 336xf32, 336xf32)
        batch_norm__264, batch_norm__265, batch_norm__266, batch_norm__267, batch_norm__268, batch_norm__269 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(depthwise_conv2d_14, parameter_249, parameter_250, parameter_251, parameter_252, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x336x7x7xf16) <- (-1x336x7x7xf16)
        hardswish_18 = paddle._C_ops.hardswish(batch_norm__264)

        # pd_op.pool2d: (-1x336x1x1xf16) <- (-1x336x7x7xf16, 2xi64)
        pool2d_7 = paddle._C_ops.pool2d(hardswish_18, constant_0, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x84x1x1xf16) <- (-1x336x1x1xf16, 84x336x1x1xf16)
        conv2d_44 = paddle._C_ops.conv2d(pool2d_7, parameter_253, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.add_: (-1x84x1x1xf16) <- (-1x84x1x1xf16, 1x84x1x1xf16)
        add__23 = paddle._C_ops.add_(conv2d_44, parameter_254)

        # pd_op.relu_: (-1x84x1x1xf16) <- (-1x84x1x1xf16)
        relu__19 = paddle._C_ops.relu_(add__23)

        # pd_op.conv2d: (-1x336x1x1xf16) <- (-1x84x1x1xf16, 336x84x1x1xf16)
        conv2d_45 = paddle._C_ops.conv2d(relu__19, parameter_255, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.add_: (-1x336x1x1xf16) <- (-1x336x1x1xf16, 1x336x1x1xf16)
        add__24 = paddle._C_ops.add_(conv2d_45, parameter_256)

        # pd_op.hardsigmoid: (-1x336x1x1xf16) <- (-1x336x1x1xf16)
        hardsigmoid_7 = paddle._C_ops.hardsigmoid(add__24, float('0.2'), float('0.5'))

        # pd_op.multiply_: (-1x336x7x7xf16) <- (-1x336x7x7xf16, -1x336x1x1xf16)
        multiply__7 = paddle._C_ops.multiply_(hardswish_18, hardsigmoid_7)

        # pd_op.conv2d: (-1x56x7x7xf16) <- (-1x336x7x7xf16, 56x336x1x1xf16)
        conv2d_46 = paddle._C_ops.conv2d(multiply__7, parameter_257, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x56x7x7xf16, 56xf32, 56xf32, 56xf32, 56xf32, None) <- (-1x56x7x7xf16, 56xf32, 56xf32, 56xf32, 56xf32)
        batch_norm__270, batch_norm__271, batch_norm__272, batch_norm__273, batch_norm__274, batch_norm__275 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_46, parameter_258, parameter_259, parameter_260, parameter_261, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.add_: (-1x56x7x7xf16) <- (-1x56x7x7xf16, -1x56x7x7xf16)
        add__25 = paddle._C_ops.add_(add__22, batch_norm__270)

        # pd_op.conv2d: (-1x336x7x7xf16) <- (-1x56x7x7xf16, 336x56x1x1xf16)
        conv2d_47 = paddle._C_ops.conv2d(add__25, parameter_262, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.batch_norm_: (-1x336x7x7xf16, 336xf32, 336xf32, 336xf32, 336xf32, None) <- (-1x336x7x7xf16, 336xf32, 336xf32, 336xf32, 336xf32)
        batch_norm__276, batch_norm__277, batch_norm__278, batch_norm__279, batch_norm__280, batch_norm__281 = (lambda x, f: f(x))(paddle._C_ops.batch_norm(conv2d_47, parameter_263, parameter_264, parameter_265, parameter_266, True, float('0.9'), float('1e-05'), 'NCHW', False, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None,None,None,None,None))

        # pd_op.hardswish: (-1x336x7x7xf16) <- (-1x336x7x7xf16)
        hardswish_19 = paddle._C_ops.hardswish(batch_norm__276)

        # pd_op.pool2d: (-1x336x1x1xf16) <- (-1x336x7x7xf16, 2xi64)
        pool2d_8 = paddle._C_ops.pool2d(hardswish_19, constant_0, [1, 1], [0, 0], False, True, 'NCHW', 'avg', False, True, 'EXPLICIT')

        # pd_op.conv2d: (-1x1280x1x1xf16) <- (-1x336x1x1xf16, 1280x336x1x1xf16)
        conv2d_48 = paddle._C_ops.conv2d(pool2d_8, parameter_267, [1, 1], [0, 0], 'EXPLICIT', [1, 1], 1, 'NCHW')

        # pd_op.hardswish: (-1x1280x1x1xf16) <- (-1x1280x1x1xf16)
        hardswish_20 = paddle._C_ops.hardswish(conv2d_48)

        # pd_op.dropout: (-1x1280x1x1xf16, None) <- (-1x1280x1x1xf16, None, 1xf32)
        dropout_0, dropout_1 = (lambda x, f: f(x))(paddle._C_ops.dropout(hardswish_20, None, constant_1, True, 'downgrade_in_infer', 0, False), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.flatten_: (-1x1280xf16, None) <- (-1x1280x1x1xf16)
        flatten__0, flatten__1 = (lambda x, f: f(x))(paddle._C_ops.flatten_(dropout_0, 1, 3), lambda out: out if isinstance(out, (list, tuple)) else (out, None))

        # pd_op.matmul: (-1x1000xf16) <- (-1x1280xf16, 1280x1000xf16)
        matmul_0 = paddle.matmul(flatten__0, parameter_268, transpose_x=False, transpose_y=False)

        # pd_op.add_: (-1x1000xf16) <- (-1x1000xf16, 1000xf16)
        add__26 = paddle._C_ops.add_(matmul_0, parameter_269)

        # pd_op.softmax_: (-1x1000xf16) <- (-1x1000xf16)
        softmax__0 = paddle._C_ops.softmax_(add__26, -1)

        # pd_op.cast: (-1x1000xf32) <- (-1x1000xf16)
        cast_1 = paddle._C_ops.cast(softmax__0, paddle.float32)
        return cast_1



def GetEnvVarEnableJit():
    enable_jit = os.getenv('PADDLE_DEBUG_ENABLE_JIT')
    return enable_jit not in {
        "0",
        "False",
        "false",
        "OFF",
    }

def GetEnvVarEnableCinn():
    enable_cinn = os.getenv('PADDLE_DEBUG_ENABLE_CINN')
    return enable_cinn not in {
        "0",
        "False",
        "false",
        "OFF",
    }


def GetTolerance(dtype):
    if dtype == np.float16:
        return GetFloat16Tolerance()
    if dtype == np.float32:
        return GetFloat32Tolerance()
    return 1e-6

def GetFloat16Tolerance():
    try:
        return float(os.getenv('PADDLE_DEBUG_FLOAT16_TOL'))
    except:
        return 1e-3

def GetFloat32Tolerance():
    try:
        return float(os.getenv('PADDLE_DEBUG_FLOAT32_TOL'))
    except:
        return 1e-6

def IsInteger(dtype):
    return np.dtype(dtype).char in np.typecodes['AllInteger']


class CinnTestBase:
    def setUp(self):
        paddle.seed(2024)
        self.prepare_data()

    def _test_entry(self):
        dy_outs = self.entry(use_cinn=False)
        cinn_outs = self.entry(use_cinn=GetEnvVarEnableCinn())

        for cinn_out, dy_out in zip(cinn_outs, dy_outs):
          if type(cinn_out) is list and type(dy_out) is list:
            for x, y in zip(cinn_out, dy_out):
              self.assert_all_close(x, y)
          else:
            self.assert_all_close(cinn_out, dy_out)

    def assert_all_close(self, x, y):
        if (hasattr(x, "numpy") and hasattr(y, "numpy")):
            x_numpy = x.numpy()
            y_numpy = y.numpy()
            assert x_numpy.dtype == y_numpy.dtype
            if IsInteger(x_numpy.dtype):
                np.testing.assert_equal(x_numpy, y_numpy)
            else:
                tol = GetTolerance(x_numpy.dtype)
                np.testing.assert_allclose(x_numpy, y_numpy, atol=tol, rtol=tol)
        else:
            assert x == y

class ModuleOp(paddle.nn.Layer, BlockEntries):
    def __init__(self):
        super().__init__()

    def forward(self, constant_1, parameter_256, parameter_254, parameter_237, parameter_235, parameter_218, parameter_216, parameter_199, parameter_197, parameter_180, parameter_178, parameter_101, parameter_99, parameter_82, parameter_80, parameter_63, parameter_61, constant_0, parameter_0, parameter_4, parameter_1, parameter_3, parameter_2, parameter_5, parameter_9, parameter_6, parameter_8, parameter_7, parameter_10, parameter_14, parameter_11, parameter_13, parameter_12, parameter_15, parameter_19, parameter_16, parameter_18, parameter_17, parameter_20, parameter_24, parameter_21, parameter_23, parameter_22, parameter_25, parameter_29, parameter_26, parameter_28, parameter_27, parameter_30, parameter_34, parameter_31, parameter_33, parameter_32, parameter_35, parameter_39, parameter_36, parameter_38, parameter_37, parameter_40, parameter_44, parameter_41, parameter_43, parameter_42, parameter_45, parameter_49, parameter_46, parameter_48, parameter_47, parameter_50, parameter_54, parameter_51, parameter_53, parameter_52, parameter_55, parameter_59, parameter_56, parameter_58, parameter_57, parameter_60, parameter_62, parameter_64, parameter_68, parameter_65, parameter_67, parameter_66, parameter_69, parameter_73, parameter_70, parameter_72, parameter_71, parameter_74, parameter_78, parameter_75, parameter_77, parameter_76, parameter_79, parameter_81, parameter_83, parameter_87, parameter_84, parameter_86, parameter_85, parameter_88, parameter_92, parameter_89, parameter_91, parameter_90, parameter_93, parameter_97, parameter_94, parameter_96, parameter_95, parameter_98, parameter_100, parameter_102, parameter_106, parameter_103, parameter_105, parameter_104, parameter_107, parameter_111, parameter_108, parameter_110, parameter_109, parameter_112, parameter_116, parameter_113, parameter_115, parameter_114, parameter_117, parameter_121, parameter_118, parameter_120, parameter_119, parameter_122, parameter_126, parameter_123, parameter_125, parameter_124, parameter_127, parameter_131, parameter_128, parameter_130, parameter_129, parameter_132, parameter_136, parameter_133, parameter_135, parameter_134, parameter_137, parameter_141, parameter_138, parameter_140, parameter_139, parameter_142, parameter_146, parameter_143, parameter_145, parameter_144, parameter_147, parameter_151, parameter_148, parameter_150, parameter_149, parameter_152, parameter_156, parameter_153, parameter_155, parameter_154, parameter_157, parameter_161, parameter_158, parameter_160, parameter_159, parameter_162, parameter_166, parameter_163, parameter_165, parameter_164, parameter_167, parameter_171, parameter_168, parameter_170, parameter_169, parameter_172, parameter_176, parameter_173, parameter_175, parameter_174, parameter_177, parameter_179, parameter_181, parameter_185, parameter_182, parameter_184, parameter_183, parameter_186, parameter_190, parameter_187, parameter_189, parameter_188, parameter_191, parameter_195, parameter_192, parameter_194, parameter_193, parameter_196, parameter_198, parameter_200, parameter_204, parameter_201, parameter_203, parameter_202, parameter_205, parameter_209, parameter_206, parameter_208, parameter_207, parameter_210, parameter_214, parameter_211, parameter_213, parameter_212, parameter_215, parameter_217, parameter_219, parameter_223, parameter_220, parameter_222, parameter_221, parameter_224, parameter_228, parameter_225, parameter_227, parameter_226, parameter_229, parameter_233, parameter_230, parameter_232, parameter_231, parameter_234, parameter_236, parameter_238, parameter_242, parameter_239, parameter_241, parameter_240, parameter_243, parameter_247, parameter_244, parameter_246, parameter_245, parameter_248, parameter_252, parameter_249, parameter_251, parameter_250, parameter_253, parameter_255, parameter_257, parameter_261, parameter_258, parameter_260, parameter_259, parameter_262, parameter_266, parameter_263, parameter_265, parameter_264, parameter_267, parameter_268, parameter_269, feed_0):
        return self.builtin_module_837_0_0(constant_1, parameter_256, parameter_254, parameter_237, parameter_235, parameter_218, parameter_216, parameter_199, parameter_197, parameter_180, parameter_178, parameter_101, parameter_99, parameter_82, parameter_80, parameter_63, parameter_61, constant_0, parameter_0, parameter_4, parameter_1, parameter_3, parameter_2, parameter_5, parameter_9, parameter_6, parameter_8, parameter_7, parameter_10, parameter_14, parameter_11, parameter_13, parameter_12, parameter_15, parameter_19, parameter_16, parameter_18, parameter_17, parameter_20, parameter_24, parameter_21, parameter_23, parameter_22, parameter_25, parameter_29, parameter_26, parameter_28, parameter_27, parameter_30, parameter_34, parameter_31, parameter_33, parameter_32, parameter_35, parameter_39, parameter_36, parameter_38, parameter_37, parameter_40, parameter_44, parameter_41, parameter_43, parameter_42, parameter_45, parameter_49, parameter_46, parameter_48, parameter_47, parameter_50, parameter_54, parameter_51, parameter_53, parameter_52, parameter_55, parameter_59, parameter_56, parameter_58, parameter_57, parameter_60, parameter_62, parameter_64, parameter_68, parameter_65, parameter_67, parameter_66, parameter_69, parameter_73, parameter_70, parameter_72, parameter_71, parameter_74, parameter_78, parameter_75, parameter_77, parameter_76, parameter_79, parameter_81, parameter_83, parameter_87, parameter_84, parameter_86, parameter_85, parameter_88, parameter_92, parameter_89, parameter_91, parameter_90, parameter_93, parameter_97, parameter_94, parameter_96, parameter_95, parameter_98, parameter_100, parameter_102, parameter_106, parameter_103, parameter_105, parameter_104, parameter_107, parameter_111, parameter_108, parameter_110, parameter_109, parameter_112, parameter_116, parameter_113, parameter_115, parameter_114, parameter_117, parameter_121, parameter_118, parameter_120, parameter_119, parameter_122, parameter_126, parameter_123, parameter_125, parameter_124, parameter_127, parameter_131, parameter_128, parameter_130, parameter_129, parameter_132, parameter_136, parameter_133, parameter_135, parameter_134, parameter_137, parameter_141, parameter_138, parameter_140, parameter_139, parameter_142, parameter_146, parameter_143, parameter_145, parameter_144, parameter_147, parameter_151, parameter_148, parameter_150, parameter_149, parameter_152, parameter_156, parameter_153, parameter_155, parameter_154, parameter_157, parameter_161, parameter_158, parameter_160, parameter_159, parameter_162, parameter_166, parameter_163, parameter_165, parameter_164, parameter_167, parameter_171, parameter_168, parameter_170, parameter_169, parameter_172, parameter_176, parameter_173, parameter_175, parameter_174, parameter_177, parameter_179, parameter_181, parameter_185, parameter_182, parameter_184, parameter_183, parameter_186, parameter_190, parameter_187, parameter_189, parameter_188, parameter_191, parameter_195, parameter_192, parameter_194, parameter_193, parameter_196, parameter_198, parameter_200, parameter_204, parameter_201, parameter_203, parameter_202, parameter_205, parameter_209, parameter_206, parameter_208, parameter_207, parameter_210, parameter_214, parameter_211, parameter_213, parameter_212, parameter_215, parameter_217, parameter_219, parameter_223, parameter_220, parameter_222, parameter_221, parameter_224, parameter_228, parameter_225, parameter_227, parameter_226, parameter_229, parameter_233, parameter_230, parameter_232, parameter_231, parameter_234, parameter_236, parameter_238, parameter_242, parameter_239, parameter_241, parameter_240, parameter_243, parameter_247, parameter_244, parameter_246, parameter_245, parameter_248, parameter_252, parameter_249, parameter_251, parameter_250, parameter_253, parameter_255, parameter_257, parameter_261, parameter_258, parameter_260, parameter_259, parameter_262, parameter_266, parameter_263, parameter_265, parameter_264, parameter_267, parameter_268, parameter_269, feed_0)

@unittest.skipIf(need_skip, skip_message)
class Test_builtin_module_837_0_0(CinnTestBase, unittest.TestCase):
    def prepare_data(self):
        self.inputs = [
            # constant_1
            paddle.uniform([1], dtype='float32', min=0, max=0.5),
            # parameter_256
            paddle.uniform([1, 336, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_254
            paddle.uniform([1, 84, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_237
            paddle.uniform([1, 336, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_235
            paddle.uniform([1, 84, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_218
            paddle.uniform([1, 232, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_216
            paddle.uniform([1, 58, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_199
            paddle.uniform([1, 232, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_197
            paddle.uniform([1, 58, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_180
            paddle.uniform([1, 168, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_178
            paddle.uniform([1, 42, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_101
            paddle.uniform([1, 40, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_99
            paddle.uniform([1, 10, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_82
            paddle.uniform([1, 40, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_80
            paddle.uniform([1, 10, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_63
            paddle.uniform([1, 24, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_61
            paddle.uniform([1, 6, 1, 1], dtype='float16', min=0, max=0.5),
            # constant_0
            paddle.to_tensor([1, 1], dtype='int64').reshape([2]),
            # parameter_0
            paddle.uniform([8, 3, 3, 3], dtype='float16', min=0, max=0.5),
            # parameter_4
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_1
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_3
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_2
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_5
            paddle.uniform([8, 8, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_9
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_6
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_8
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_7
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_10
            paddle.uniform([8, 1, 3, 3], dtype='float16', min=0, max=0.5),
            # parameter_14
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_11
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_13
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_12
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_15
            paddle.uniform([8, 8, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_19
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_16
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_18
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_17
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_20
            paddle.uniform([24, 8, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_24
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_21
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_23
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_22
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_25
            paddle.uniform([24, 1, 3, 3], dtype='float16', min=0, max=0.5),
            # parameter_29
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_26
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_28
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_27
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_30
            paddle.uniform([8, 24, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_34
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_31
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_33
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_32
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_35
            paddle.uniform([24, 8, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_39
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_36
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_38
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_37
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_40
            paddle.uniform([24, 1, 3, 3], dtype='float16', min=0, max=0.5),
            # parameter_44
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_41
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_43
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_42
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_45
            paddle.uniform([8, 24, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_49
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_46
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_48
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_47
            paddle.uniform([8], dtype='float32', min=0, max=0.5),
            # parameter_50
            paddle.uniform([24, 8, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_54
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_51
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_53
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_52
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_55
            paddle.uniform([24, 1, 5, 5], dtype='float16', min=0, max=0.5),
            # parameter_59
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_56
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_58
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_57
            paddle.uniform([24], dtype='float32', min=0, max=0.5),
            # parameter_60
            paddle.uniform([6, 24, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_62
            paddle.uniform([24, 6, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_64
            paddle.uniform([16, 24, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_68
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_65
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_67
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_66
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_69
            paddle.uniform([40, 16, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_73
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_70
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_72
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_71
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_74
            paddle.uniform([40, 1, 5, 5], dtype='float16', min=0, max=0.5),
            # parameter_78
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_75
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_77
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_76
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_79
            paddle.uniform([10, 40, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_81
            paddle.uniform([40, 10, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_83
            paddle.uniform([16, 40, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_87
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_84
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_86
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_85
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_88
            paddle.uniform([40, 16, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_92
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_89
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_91
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_90
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_93
            paddle.uniform([40, 1, 5, 5], dtype='float16', min=0, max=0.5),
            # parameter_97
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_94
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_96
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_95
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_98
            paddle.uniform([10, 40, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_100
            paddle.uniform([40, 10, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_102
            paddle.uniform([16, 40, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_106
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_103
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_105
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_104
            paddle.uniform([16], dtype='float32', min=0, max=0.5),
            # parameter_107
            paddle.uniform([88, 16, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_111
            paddle.uniform([88], dtype='float32', min=0, max=0.5),
            # parameter_108
            paddle.uniform([88], dtype='float32', min=0, max=0.5),
            # parameter_110
            paddle.uniform([88], dtype='float32', min=0, max=0.5),
            # parameter_109
            paddle.uniform([88], dtype='float32', min=0, max=0.5),
            # parameter_112
            paddle.uniform([88, 1, 3, 3], dtype='float16', min=0, max=0.5),
            # parameter_116
            paddle.uniform([88], dtype='float32', min=0, max=0.5),
            # parameter_113
            paddle.uniform([88], dtype='float32', min=0, max=0.5),
            # parameter_115
            paddle.uniform([88], dtype='float32', min=0, max=0.5),
            # parameter_114
            paddle.uniform([88], dtype='float32', min=0, max=0.5),
            # parameter_117
            paddle.uniform([32, 88, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_121
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_118
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_120
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_119
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_122
            paddle.uniform([72, 32, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_126
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_123
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_125
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_124
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_127
            paddle.uniform([72, 1, 3, 3], dtype='float16', min=0, max=0.5),
            # parameter_131
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_128
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_130
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_129
            paddle.uniform([72], dtype='float32', min=0, max=0.5),
            # parameter_132
            paddle.uniform([32, 72, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_136
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_133
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_135
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_134
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_137
            paddle.uniform([64, 32, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_141
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_138
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_140
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_139
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_142
            paddle.uniform([64, 1, 3, 3], dtype='float16', min=0, max=0.5),
            # parameter_146
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_143
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_145
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_144
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_147
            paddle.uniform([32, 64, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_151
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_148
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_150
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_149
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_152
            paddle.uniform([64, 32, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_156
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_153
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_155
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_154
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_157
            paddle.uniform([64, 1, 3, 3], dtype='float16', min=0, max=0.5),
            # parameter_161
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_158
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_160
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_159
            paddle.uniform([64], dtype='float32', min=0, max=0.5),
            # parameter_162
            paddle.uniform([32, 64, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_166
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_163
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_165
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_164
            paddle.uniform([32], dtype='float32', min=0, max=0.5),
            # parameter_167
            paddle.uniform([168, 32, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_171
            paddle.uniform([168], dtype='float32', min=0, max=0.5),
            # parameter_168
            paddle.uniform([168], dtype='float32', min=0, max=0.5),
            # parameter_170
            paddle.uniform([168], dtype='float32', min=0, max=0.5),
            # parameter_169
            paddle.uniform([168], dtype='float32', min=0, max=0.5),
            # parameter_172
            paddle.uniform([168, 1, 3, 3], dtype='float16', min=0, max=0.5),
            # parameter_176
            paddle.uniform([168], dtype='float32', min=0, max=0.5),
            # parameter_173
            paddle.uniform([168], dtype='float32', min=0, max=0.5),
            # parameter_175
            paddle.uniform([168], dtype='float32', min=0, max=0.5),
            # parameter_174
            paddle.uniform([168], dtype='float32', min=0, max=0.5),
            # parameter_177
            paddle.uniform([42, 168, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_179
            paddle.uniform([168, 42, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_181
            paddle.uniform([40, 168, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_185
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_182
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_184
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_183
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_186
            paddle.uniform([232, 40, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_190
            paddle.uniform([232], dtype='float32', min=0, max=0.5),
            # parameter_187
            paddle.uniform([232], dtype='float32', min=0, max=0.5),
            # parameter_189
            paddle.uniform([232], dtype='float32', min=0, max=0.5),
            # parameter_188
            paddle.uniform([232], dtype='float32', min=0, max=0.5),
            # parameter_191
            paddle.uniform([232, 1, 3, 3], dtype='float16', min=0, max=0.5),
            # parameter_195
            paddle.uniform([232], dtype='float32', min=0, max=0.5),
            # parameter_192
            paddle.uniform([232], dtype='float32', min=0, max=0.5),
            # parameter_194
            paddle.uniform([232], dtype='float32', min=0, max=0.5),
            # parameter_193
            paddle.uniform([232], dtype='float32', min=0, max=0.5),
            # parameter_196
            paddle.uniform([58, 232, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_198
            paddle.uniform([232, 58, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_200
            paddle.uniform([40, 232, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_204
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_201
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_203
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_202
            paddle.uniform([40], dtype='float32', min=0, max=0.5),
            # parameter_205
            paddle.uniform([232, 40, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_209
            paddle.uniform([232], dtype='float32', min=0, max=0.5),
            # parameter_206
            paddle.uniform([232], dtype='float32', min=0, max=0.5),
            # parameter_208
            paddle.uniform([232], dtype='float32', min=0, max=0.5),
            # parameter_207
            paddle.uniform([232], dtype='float32', min=0, max=0.5),
            # parameter_210
            paddle.uniform([232, 1, 5, 5], dtype='float16', min=0, max=0.5),
            # parameter_214
            paddle.uniform([232], dtype='float32', min=0, max=0.5),
            # parameter_211
            paddle.uniform([232], dtype='float32', min=0, max=0.5),
            # parameter_213
            paddle.uniform([232], dtype='float32', min=0, max=0.5),
            # parameter_212
            paddle.uniform([232], dtype='float32', min=0, max=0.5),
            # parameter_215
            paddle.uniform([58, 232, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_217
            paddle.uniform([232, 58, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_219
            paddle.uniform([56, 232, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_223
            paddle.uniform([56], dtype='float32', min=0, max=0.5),
            # parameter_220
            paddle.uniform([56], dtype='float32', min=0, max=0.5),
            # parameter_222
            paddle.uniform([56], dtype='float32', min=0, max=0.5),
            # parameter_221
            paddle.uniform([56], dtype='float32', min=0, max=0.5),
            # parameter_224
            paddle.uniform([336, 56, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_228
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_225
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_227
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_226
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_229
            paddle.uniform([336, 1, 5, 5], dtype='float16', min=0, max=0.5),
            # parameter_233
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_230
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_232
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_231
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_234
            paddle.uniform([84, 336, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_236
            paddle.uniform([336, 84, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_238
            paddle.uniform([56, 336, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_242
            paddle.uniform([56], dtype='float32', min=0, max=0.5),
            # parameter_239
            paddle.uniform([56], dtype='float32', min=0, max=0.5),
            # parameter_241
            paddle.uniform([56], dtype='float32', min=0, max=0.5),
            # parameter_240
            paddle.uniform([56], dtype='float32', min=0, max=0.5),
            # parameter_243
            paddle.uniform([336, 56, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_247
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_244
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_246
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_245
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_248
            paddle.uniform([336, 1, 5, 5], dtype='float16', min=0, max=0.5),
            # parameter_252
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_249
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_251
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_250
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_253
            paddle.uniform([84, 336, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_255
            paddle.uniform([336, 84, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_257
            paddle.uniform([56, 336, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_261
            paddle.uniform([56], dtype='float32', min=0, max=0.5),
            # parameter_258
            paddle.uniform([56], dtype='float32', min=0, max=0.5),
            # parameter_260
            paddle.uniform([56], dtype='float32', min=0, max=0.5),
            # parameter_259
            paddle.uniform([56], dtype='float32', min=0, max=0.5),
            # parameter_262
            paddle.uniform([336, 56, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_266
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_263
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_265
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_264
            paddle.uniform([336], dtype='float32', min=0, max=0.5),
            # parameter_267
            paddle.uniform([1280, 336, 1, 1], dtype='float16', min=0, max=0.5),
            # parameter_268
            paddle.uniform([1280, 1000], dtype='float16', min=0, max=0.5),
            # parameter_269
            paddle.uniform([1000], dtype='float16', min=0, max=0.5),
            # feed_0
            paddle.uniform([1, 3, 224, 224], dtype='float32', min=0, max=0.5),
        ]
        for input in self.inputs:
            input.stop_gradient = True

    def apply_to_static(self, net, use_cinn):
        build_strategy = paddle.static.BuildStrategy()
        input_spec = [
            # constant_1
            paddle.static.InputSpec(shape=[1], dtype='float32'),
            # parameter_256
            paddle.static.InputSpec(shape=[1, 336, 1, 1], dtype='float16'),
            # parameter_254
            paddle.static.InputSpec(shape=[1, 84, 1, 1], dtype='float16'),
            # parameter_237
            paddle.static.InputSpec(shape=[1, 336, 1, 1], dtype='float16'),
            # parameter_235
            paddle.static.InputSpec(shape=[1, 84, 1, 1], dtype='float16'),
            # parameter_218
            paddle.static.InputSpec(shape=[1, 232, 1, 1], dtype='float16'),
            # parameter_216
            paddle.static.InputSpec(shape=[1, 58, 1, 1], dtype='float16'),
            # parameter_199
            paddle.static.InputSpec(shape=[1, 232, 1, 1], dtype='float16'),
            # parameter_197
            paddle.static.InputSpec(shape=[1, 58, 1, 1], dtype='float16'),
            # parameter_180
            paddle.static.InputSpec(shape=[1, 168, 1, 1], dtype='float16'),
            # parameter_178
            paddle.static.InputSpec(shape=[1, 42, 1, 1], dtype='float16'),
            # parameter_101
            paddle.static.InputSpec(shape=[1, 40, 1, 1], dtype='float16'),
            # parameter_99
            paddle.static.InputSpec(shape=[1, 10, 1, 1], dtype='float16'),
            # parameter_82
            paddle.static.InputSpec(shape=[1, 40, 1, 1], dtype='float16'),
            # parameter_80
            paddle.static.InputSpec(shape=[1, 10, 1, 1], dtype='float16'),
            # parameter_63
            paddle.static.InputSpec(shape=[1, 24, 1, 1], dtype='float16'),
            # parameter_61
            paddle.static.InputSpec(shape=[1, 6, 1, 1], dtype='float16'),
            # constant_0
            paddle.static.InputSpec(shape=[2], dtype='int64'),
            # parameter_0
            paddle.static.InputSpec(shape=[8, 3, 3, 3], dtype='float16'),
            # parameter_4
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_1
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_3
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_2
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_5
            paddle.static.InputSpec(shape=[8, 8, 1, 1], dtype='float16'),
            # parameter_9
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_6
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_8
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_7
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_10
            paddle.static.InputSpec(shape=[8, 1, 3, 3], dtype='float16'),
            # parameter_14
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_11
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_13
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_12
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_15
            paddle.static.InputSpec(shape=[8, 8, 1, 1], dtype='float16'),
            # parameter_19
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_16
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_18
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_17
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_20
            paddle.static.InputSpec(shape=[24, 8, 1, 1], dtype='float16'),
            # parameter_24
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_21
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_23
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_22
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_25
            paddle.static.InputSpec(shape=[24, 1, 3, 3], dtype='float16'),
            # parameter_29
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_26
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_28
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_27
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_30
            paddle.static.InputSpec(shape=[8, 24, 1, 1], dtype='float16'),
            # parameter_34
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_31
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_33
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_32
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_35
            paddle.static.InputSpec(shape=[24, 8, 1, 1], dtype='float16'),
            # parameter_39
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_36
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_38
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_37
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_40
            paddle.static.InputSpec(shape=[24, 1, 3, 3], dtype='float16'),
            # parameter_44
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_41
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_43
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_42
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_45
            paddle.static.InputSpec(shape=[8, 24, 1, 1], dtype='float16'),
            # parameter_49
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_46
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_48
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_47
            paddle.static.InputSpec(shape=[8], dtype='float32'),
            # parameter_50
            paddle.static.InputSpec(shape=[24, 8, 1, 1], dtype='float16'),
            # parameter_54
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_51
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_53
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_52
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_55
            paddle.static.InputSpec(shape=[24, 1, 5, 5], dtype='float16'),
            # parameter_59
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_56
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_58
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_57
            paddle.static.InputSpec(shape=[24], dtype='float32'),
            # parameter_60
            paddle.static.InputSpec(shape=[6, 24, 1, 1], dtype='float16'),
            # parameter_62
            paddle.static.InputSpec(shape=[24, 6, 1, 1], dtype='float16'),
            # parameter_64
            paddle.static.InputSpec(shape=[16, 24, 1, 1], dtype='float16'),
            # parameter_68
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_65
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_67
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_66
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_69
            paddle.static.InputSpec(shape=[40, 16, 1, 1], dtype='float16'),
            # parameter_73
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_70
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_72
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_71
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_74
            paddle.static.InputSpec(shape=[40, 1, 5, 5], dtype='float16'),
            # parameter_78
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_75
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_77
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_76
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_79
            paddle.static.InputSpec(shape=[10, 40, 1, 1], dtype='float16'),
            # parameter_81
            paddle.static.InputSpec(shape=[40, 10, 1, 1], dtype='float16'),
            # parameter_83
            paddle.static.InputSpec(shape=[16, 40, 1, 1], dtype='float16'),
            # parameter_87
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_84
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_86
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_85
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_88
            paddle.static.InputSpec(shape=[40, 16, 1, 1], dtype='float16'),
            # parameter_92
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_89
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_91
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_90
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_93
            paddle.static.InputSpec(shape=[40, 1, 5, 5], dtype='float16'),
            # parameter_97
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_94
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_96
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_95
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_98
            paddle.static.InputSpec(shape=[10, 40, 1, 1], dtype='float16'),
            # parameter_100
            paddle.static.InputSpec(shape=[40, 10, 1, 1], dtype='float16'),
            # parameter_102
            paddle.static.InputSpec(shape=[16, 40, 1, 1], dtype='float16'),
            # parameter_106
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_103
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_105
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_104
            paddle.static.InputSpec(shape=[16], dtype='float32'),
            # parameter_107
            paddle.static.InputSpec(shape=[88, 16, 1, 1], dtype='float16'),
            # parameter_111
            paddle.static.InputSpec(shape=[88], dtype='float32'),
            # parameter_108
            paddle.static.InputSpec(shape=[88], dtype='float32'),
            # parameter_110
            paddle.static.InputSpec(shape=[88], dtype='float32'),
            # parameter_109
            paddle.static.InputSpec(shape=[88], dtype='float32'),
            # parameter_112
            paddle.static.InputSpec(shape=[88, 1, 3, 3], dtype='float16'),
            # parameter_116
            paddle.static.InputSpec(shape=[88], dtype='float32'),
            # parameter_113
            paddle.static.InputSpec(shape=[88], dtype='float32'),
            # parameter_115
            paddle.static.InputSpec(shape=[88], dtype='float32'),
            # parameter_114
            paddle.static.InputSpec(shape=[88], dtype='float32'),
            # parameter_117
            paddle.static.InputSpec(shape=[32, 88, 1, 1], dtype='float16'),
            # parameter_121
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_118
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_120
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_119
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_122
            paddle.static.InputSpec(shape=[72, 32, 1, 1], dtype='float16'),
            # parameter_126
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_123
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_125
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_124
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_127
            paddle.static.InputSpec(shape=[72, 1, 3, 3], dtype='float16'),
            # parameter_131
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_128
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_130
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_129
            paddle.static.InputSpec(shape=[72], dtype='float32'),
            # parameter_132
            paddle.static.InputSpec(shape=[32, 72, 1, 1], dtype='float16'),
            # parameter_136
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_133
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_135
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_134
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_137
            paddle.static.InputSpec(shape=[64, 32, 1, 1], dtype='float16'),
            # parameter_141
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_138
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_140
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_139
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_142
            paddle.static.InputSpec(shape=[64, 1, 3, 3], dtype='float16'),
            # parameter_146
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_143
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_145
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_144
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_147
            paddle.static.InputSpec(shape=[32, 64, 1, 1], dtype='float16'),
            # parameter_151
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_148
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_150
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_149
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_152
            paddle.static.InputSpec(shape=[64, 32, 1, 1], dtype='float16'),
            # parameter_156
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_153
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_155
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_154
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_157
            paddle.static.InputSpec(shape=[64, 1, 3, 3], dtype='float16'),
            # parameter_161
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_158
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_160
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_159
            paddle.static.InputSpec(shape=[64], dtype='float32'),
            # parameter_162
            paddle.static.InputSpec(shape=[32, 64, 1, 1], dtype='float16'),
            # parameter_166
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_163
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_165
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_164
            paddle.static.InputSpec(shape=[32], dtype='float32'),
            # parameter_167
            paddle.static.InputSpec(shape=[168, 32, 1, 1], dtype='float16'),
            # parameter_171
            paddle.static.InputSpec(shape=[168], dtype='float32'),
            # parameter_168
            paddle.static.InputSpec(shape=[168], dtype='float32'),
            # parameter_170
            paddle.static.InputSpec(shape=[168], dtype='float32'),
            # parameter_169
            paddle.static.InputSpec(shape=[168], dtype='float32'),
            # parameter_172
            paddle.static.InputSpec(shape=[168, 1, 3, 3], dtype='float16'),
            # parameter_176
            paddle.static.InputSpec(shape=[168], dtype='float32'),
            # parameter_173
            paddle.static.InputSpec(shape=[168], dtype='float32'),
            # parameter_175
            paddle.static.InputSpec(shape=[168], dtype='float32'),
            # parameter_174
            paddle.static.InputSpec(shape=[168], dtype='float32'),
            # parameter_177
            paddle.static.InputSpec(shape=[42, 168, 1, 1], dtype='float16'),
            # parameter_179
            paddle.static.InputSpec(shape=[168, 42, 1, 1], dtype='float16'),
            # parameter_181
            paddle.static.InputSpec(shape=[40, 168, 1, 1], dtype='float16'),
            # parameter_185
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_182
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_184
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_183
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_186
            paddle.static.InputSpec(shape=[232, 40, 1, 1], dtype='float16'),
            # parameter_190
            paddle.static.InputSpec(shape=[232], dtype='float32'),
            # parameter_187
            paddle.static.InputSpec(shape=[232], dtype='float32'),
            # parameter_189
            paddle.static.InputSpec(shape=[232], dtype='float32'),
            # parameter_188
            paddle.static.InputSpec(shape=[232], dtype='float32'),
            # parameter_191
            paddle.static.InputSpec(shape=[232, 1, 3, 3], dtype='float16'),
            # parameter_195
            paddle.static.InputSpec(shape=[232], dtype='float32'),
            # parameter_192
            paddle.static.InputSpec(shape=[232], dtype='float32'),
            # parameter_194
            paddle.static.InputSpec(shape=[232], dtype='float32'),
            # parameter_193
            paddle.static.InputSpec(shape=[232], dtype='float32'),
            # parameter_196
            paddle.static.InputSpec(shape=[58, 232, 1, 1], dtype='float16'),
            # parameter_198
            paddle.static.InputSpec(shape=[232, 58, 1, 1], dtype='float16'),
            # parameter_200
            paddle.static.InputSpec(shape=[40, 232, 1, 1], dtype='float16'),
            # parameter_204
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_201
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_203
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_202
            paddle.static.InputSpec(shape=[40], dtype='float32'),
            # parameter_205
            paddle.static.InputSpec(shape=[232, 40, 1, 1], dtype='float16'),
            # parameter_209
            paddle.static.InputSpec(shape=[232], dtype='float32'),
            # parameter_206
            paddle.static.InputSpec(shape=[232], dtype='float32'),
            # parameter_208
            paddle.static.InputSpec(shape=[232], dtype='float32'),
            # parameter_207
            paddle.static.InputSpec(shape=[232], dtype='float32'),
            # parameter_210
            paddle.static.InputSpec(shape=[232, 1, 5, 5], dtype='float16'),
            # parameter_214
            paddle.static.InputSpec(shape=[232], dtype='float32'),
            # parameter_211
            paddle.static.InputSpec(shape=[232], dtype='float32'),
            # parameter_213
            paddle.static.InputSpec(shape=[232], dtype='float32'),
            # parameter_212
            paddle.static.InputSpec(shape=[232], dtype='float32'),
            # parameter_215
            paddle.static.InputSpec(shape=[58, 232, 1, 1], dtype='float16'),
            # parameter_217
            paddle.static.InputSpec(shape=[232, 58, 1, 1], dtype='float16'),
            # parameter_219
            paddle.static.InputSpec(shape=[56, 232, 1, 1], dtype='float16'),
            # parameter_223
            paddle.static.InputSpec(shape=[56], dtype='float32'),
            # parameter_220
            paddle.static.InputSpec(shape=[56], dtype='float32'),
            # parameter_222
            paddle.static.InputSpec(shape=[56], dtype='float32'),
            # parameter_221
            paddle.static.InputSpec(shape=[56], dtype='float32'),
            # parameter_224
            paddle.static.InputSpec(shape=[336, 56, 1, 1], dtype='float16'),
            # parameter_228
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_225
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_227
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_226
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_229
            paddle.static.InputSpec(shape=[336, 1, 5, 5], dtype='float16'),
            # parameter_233
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_230
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_232
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_231
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_234
            paddle.static.InputSpec(shape=[84, 336, 1, 1], dtype='float16'),
            # parameter_236
            paddle.static.InputSpec(shape=[336, 84, 1, 1], dtype='float16'),
            # parameter_238
            paddle.static.InputSpec(shape=[56, 336, 1, 1], dtype='float16'),
            # parameter_242
            paddle.static.InputSpec(shape=[56], dtype='float32'),
            # parameter_239
            paddle.static.InputSpec(shape=[56], dtype='float32'),
            # parameter_241
            paddle.static.InputSpec(shape=[56], dtype='float32'),
            # parameter_240
            paddle.static.InputSpec(shape=[56], dtype='float32'),
            # parameter_243
            paddle.static.InputSpec(shape=[336, 56, 1, 1], dtype='float16'),
            # parameter_247
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_244
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_246
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_245
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_248
            paddle.static.InputSpec(shape=[336, 1, 5, 5], dtype='float16'),
            # parameter_252
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_249
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_251
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_250
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_253
            paddle.static.InputSpec(shape=[84, 336, 1, 1], dtype='float16'),
            # parameter_255
            paddle.static.InputSpec(shape=[336, 84, 1, 1], dtype='float16'),
            # parameter_257
            paddle.static.InputSpec(shape=[56, 336, 1, 1], dtype='float16'),
            # parameter_261
            paddle.static.InputSpec(shape=[56], dtype='float32'),
            # parameter_258
            paddle.static.InputSpec(shape=[56], dtype='float32'),
            # parameter_260
            paddle.static.InputSpec(shape=[56], dtype='float32'),
            # parameter_259
            paddle.static.InputSpec(shape=[56], dtype='float32'),
            # parameter_262
            paddle.static.InputSpec(shape=[336, 56, 1, 1], dtype='float16'),
            # parameter_266
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_263
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_265
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_264
            paddle.static.InputSpec(shape=[336], dtype='float32'),
            # parameter_267
            paddle.static.InputSpec(shape=[1280, 336, 1, 1], dtype='float16'),
            # parameter_268
            paddle.static.InputSpec(shape=[1280, 1000], dtype='float16'),
            # parameter_269
            paddle.static.InputSpec(shape=[1000], dtype='float16'),
            # feed_0
            paddle.static.InputSpec(shape=[None, 3, 224, 224], dtype='float32'),
        ]
        build_strategy.build_cinn_pass = use_cinn
        return paddle.jit.to_static(
            net,
            input_spec=input_spec,
            build_strategy=build_strategy,
            full_graph=True,
        )

    def entry(self, use_cinn):
        net = ModuleOp()
        if GetEnvVarEnableJit():
            net = self.apply_to_static(net, use_cinn)
        paddle.seed(2024)
        out = net(*self.inputs)
        return out

    def test_entry(self):
        if AthenaTryRunEnabled():
            if try_run_exit_code == 0:
                # All unittest cases passed.
                return
            if try_run_exit_code < 0:
                # program panicked.
                raise RuntimeError(f"panicked. panic stderr have been reported by the unittest `TestTryRun.test_panic`.")
        self._test_entry()

if __name__ == '__main__':
    unittest.main()