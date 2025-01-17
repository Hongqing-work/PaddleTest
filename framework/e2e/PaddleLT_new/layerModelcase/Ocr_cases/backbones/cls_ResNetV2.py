import paddle
import numpy as np
import PaddleOCR.ppocr.modeling.backbones as backbones
from PaddleOCR.ppocr.modeling.backbones.rec_resnetv2 import StdConv2dSame


def LayerCase():
    """模型库中间态"""
    model = backbones.build_backbone(
        config={
            "name": "ResNetV2",
            "layers": [2, 3, 7],
            "num_classes": 0,
            "global_pool": "",
            "in_chans": 3,
            "preact": False,
            "stem_type": "same",
            "conv_layer": StdConv2dSame,
            "is_export": False,
        },
        model_type="cls",
    )
    return model


def create_inputspec():
    inputspec = (paddle.static.InputSpec(shape=(-1, 3, 224, 224), dtype=paddle.float32, stop_gradient=False),)
    return inputspec


def create_tensor_inputs():
    inputs = (paddle.rand(shape=[1, 3, 224, 224], dtype=paddle.float32),)
    return inputs


def create_numpy_inputs():
    inputs = (np.random.random(size=[1, 3, 224, 224]).astype("float32"),)
    return inputs
