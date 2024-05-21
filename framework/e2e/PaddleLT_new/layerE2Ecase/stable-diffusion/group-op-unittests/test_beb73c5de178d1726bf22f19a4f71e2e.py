import os
os.environ['FLAGS_cinn_new_group_scheduler'] = '1'
os.environ['FLAGS_group_schedule_tiling_first'] = '1'
os.environ['FLAGS_prim_all'] = 'true'
os.environ['FLAGS_prim_enable_dynamic'] = '1'
os.environ['FLAGS_enable_pir_api'] = '1'
os.environ['FLAGS_cinn_bucket_compile'] = '1'

import unittest
import numpy as np
import paddle

def NumCurrentUnittestOperations():
    return 10 # number-of-ops

def GetPaddleDebugNumAllowedOps():
    try:
        return int(os.getenv('PADDLE_DEBUG_NUM_ALLOWED_OPS'))
    except:
        return None

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


paddle_debug_num_allowed_ops = GetPaddleDebugNumAllowedOps()

def FastReturn(i):
    return (
        type(paddle_debug_num_allowed_ops) is int
        and i >= paddle_debug_num_allowed_ops
    )

class GroupOp(paddle.nn.Layer):
    def __init__(self):
        super().__init__()

    def forward(self, matmul_0, parameter_0):

        if FastReturn(0):
            return matmul_0, parameter_0

        #  type: (-1x-1x3072xf16) <- (-1x-1x3072xf16, 3072xf16)
        # shape: ([S0, S1, 3072]) <- ([S0, S1, 3072], [3072])
        #  data: (None) <- (None, None)
        add_0 = matmul_0 + parameter_0

        if FastReturn(1):
            return add_0

        #  type: (-1x-1x3072xf16) <- (-1x-1x3072xf16)
        # shape: ([S0, S1, 3072]) <- ([S0, S1, 3072])
        #  data: (None) <- (None)
        scale_0 = add_0 * 1.702 + 0

        if FastReturn(2):
            return add_0, scale_0

        #  type: (-1x-1x3072xf32) <- (-1x-1x3072xf16)
        # shape: ([S0, S1, 3072]) <- ([S0, S1, 3072])
        #  data: (None) <- (None)
        cast_0 = paddle.cast(scale_0, dtype='float32')

        if FastReturn(3):
            return add_0, cast_0

        #  type: (1xf32) <- ()
        # shape: ([1]) <- ()
        #  data: ([1]) <- ()
        full_0 = paddle.full(shape=[1], dtype='float32', fill_value=1)

        if FastReturn(4):
            return add_0, cast_0, full_0

        #  type: (-1x-1x3072xf32) <- (-1x-1x3072xf32)
        # shape: ([S0, S1, 3072]) <- ([S0, S1, 3072])
        #  data: (None) <- (None)
        scale_1 = cast_0 * -1 + 0

        if FastReturn(5):
            return add_0, full_0, scale_1

        #  type: (-1x-1x3072xf32) <- (-1x-1x3072xf32)
        # shape: ([S0, S1, 3072]) <- ([S0, S1, 3072])
        #  data: (None) <- (None)
        exp_0 = paddle.exp(scale_1)

        if FastReturn(6):
            return add_0, full_0, exp_0

        #  type: (-1x-1x3072xf32) <- (1xf32, -1x-1x3072xf32)
        # shape: ([S0, S1, 3072]) <- ([1], [S0, S1, 3072])
        #  data: (None) <- ([1], None)
        add_1 = full_0 + exp_0

        if FastReturn(7):
            return add_0, full_0, add_1

        #  type: (-1x-1x3072xf32) <- (1xf32, -1x-1x3072xf32)
        # shape: ([S0, S1, 3072]) <- ([1], [S0, S1, 3072])
        #  data: (None) <- ([1], None)
        divide_0 = full_0 / add_1

        if FastReturn(8):
            return add_0, divide_0

        #  type: (-1x-1x3072xf16) <- (-1x-1x3072xf32)
        # shape: ([S0, S1, 3072]) <- ([S0, S1, 3072])
        #  data: (None) <- (None)
        cast_1 = paddle.cast(divide_0, dtype='float16')

        if FastReturn(9):
            return add_0, cast_1

        #  type: (-1x-1x3072xf16) <- (-1x-1x3072xf16, -1x-1x3072xf16)
        # shape: ([S0, S1, 3072]) <- ([S0, S1, 3072], [S0, S1, 3072])
        #  data: (None) <- (None, None)
        multiply_0 = add_0 * cast_1

        #  type: () <- (-1x-1x3072xf16)
        # shape: () <- ([S0, S1, 3072])
        #  data: () <- (None)
        return multiply_0


class TestGroupOp(unittest.TestCase):
    def setUp(self):
        paddle.seed(2024)
        self.prepare_data()

    def prepare_data(self):
        self.inputs = [
            paddle.uniform([2, 2, 3072], dtype='float16', min=-0.5, max=0.5),
            paddle.uniform([3072], dtype='float16', min=-0.5, max=0.5),
        ]
        for input in self.inputs:
          input.stop_gradient = True

    def apply_to_static(self, net, use_cinn):
        build_strategy = paddle.static.BuildStrategy()
        input_spec = [
            paddle.static.InputSpec(shape=[None, None, 3072], dtype='float16'),
            paddle.static.InputSpec(shape=[3072], dtype='float16'),
        ]
        build_strategy.build_cinn_pass = use_cinn
        return paddle.jit.to_static(
            net,
            input_spec=input_spec,
            build_strategy=build_strategy,
            full_graph=True,
        )

    def train(self, use_cinn):
        net = GroupOp()
        net.eval()
        if GetEnvVarEnableJit():
            net = self.apply_to_static(net, use_cinn)
        out = net(*self.inputs)
        return out

    def test_train(self):
        dy_outs = self.train(use_cinn=False)
        cinn_outs = self.train(use_cinn=GetEnvVarEnableCinn())

        for cinn_out, dy_out in zip(cinn_outs, dy_outs):
          if type(cinn_out) is list and type(dy_out) is list:
            for x, y in zip(cinn_out, dy_out):
              self.assert_all_close(x, y)
          else:
            self.assert_all_close(cinn_out, dy_out)

    def assert_all_close(self, x, y):
        if (hasattr(x, "numpy") and hasattr(y, "numpy")):
            np.testing.assert_allclose(x.numpy(), y.numpy(), atol=1e-6)
        else:
            assert x == y


if __name__ == '__main__':
    unittest.main()