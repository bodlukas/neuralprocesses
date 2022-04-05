from functools import partial
from typing import Optional, Union

import lab.torch as B
import numpy as np
import torch
from plum import convert

from .. import _dispatch

__all__ = ["num_params", "Module"]


@_dispatch
def num_params(model: torch.nn.Module):
    """Get the number of parameters.

    Args:
        model (:class:`tf.keras.Model`): PyTorch model.

    Returns:
        int: Number of parameters.
    """
    return sum([int(np.prod(p.shape)) for p in model.parameters()])


def ConvNd(
    dim: int,
    in_channels: int,
    out_channels: int,
    kernel: int,
    stride: int = 1,
    dilation: int = 1,
    groups: int = 1,
    bias: bool = True,
    transposed: bool = False,
    output_padding: Optional[int] = None,
    dtype=None,
):
    """Convolutional layer.

    Args:
        dim (int): Dimensionality.
        in_channels (int): Number of input channels.
        out_channels (int): Number of output channels.
        kernel (int): Kernel size.
        stride (int, optional): Stride.
        dilation (int, optional): Dilation.
        groups (int, optional): Number of groups.
        bias (bool, optional): Use a bias. Defaults to `True`.
        transposed (bool, optional): Transposed convolution. Defaults to `False`.
        output_padding (int, optional): Output padding.
        dtype (dtype, optional): Data type.

    Returns:
        object: Convolutional layer.
    """
    # Only set `output_padding` if it is given.
    additional_args = {}
    if output_padding is not None:
        additional_args["output_padding"] = output_padding

    # Use same-padding.
    if kernel % 2 != 1:
        raise ValueError("Kernel size must be odd to achieve same-padding.")

    # Get the right layer kind.
    if transposed:
        suffix = "Transpose"
    else:
        suffix = ""

    return getattr(torch.nn, f"Conv{suffix}{dim}d")(
        in_channels=in_channels,
        out_channels=out_channels,
        kernel_size=kernel,
        stride=stride,
        padding=kernel // 2,
        dilation=dilation,
        groups=groups,
        bias=bias,
        dtype=dtype,
        **additional_args,
    )


def UpSamplingNd(
    size: int = 2,
    interp_method: str = "bilinear",
    dtype=None,
):
    """Up-sampling layer.

    Args:
        dim (int): Dimensionality.
        size (int, optional): Up-sampling factor. Defaults to `2`.
        interp_method (str, optional): Interpolation method. Can be set to "bilinear".
            Defaults to "nearest'.
        dtype (dtype): Data type.

    Returns:
        object: Up-sampling layer.
    """
    return getattr(torch.nn, "Upsample")(
        # `scale_factor` is applied to each dimension automatically: it doesn't need to
        # be repeated.
        scale_factor=size,
        mode=interp_method,
    )


def AvgPoolNd(
    dim: int,
    kernel: int,
    stride: Union[None, int] = None,
    dtype=None,
):
    """Average pooling layer.

    Args:
        dim (int): Dimensionality.
        kernel (int): Kernel size.
        stride (int, optional): Stride.
        dtype (dtype): Data type.

    Returns:
        object: Average pooling layer.
    """
    return getattr(torch.nn, f"AvgPool{dim}d")(
        kernel_size=kernel,
        stride=stride,
        padding=0,
    )


def _is_lambda(f):
    lam = lambda: None
    return isinstance(f, type(lam)) and f.__name__ == lam.__name__


class _LambdaModule(torch.nn.Module):
    def __init__(self, f):
        super().__init__()
        self.f = f

    def forward(self, x):
        return self.f(x)


class Interface:
    """PyTorch interface."""

    ReLU = torch.nn.ReLU

    Sequential = torch.nn.Sequential

    @staticmethod
    def ModuleList(modules):
        """Make a list of modules whose parameters are tracked.

        Args:
            modules (list): List of modules.

        Returns:
            `torch.nn.ModuleList`: List of `modules` whose parameters are tracked.
        """
        modules = [_LambdaModule(m) if _is_lambda(m) else m for m in modules]
        return torch.nn.ModuleList(modules)

    Linear = torch.nn.Linear

    Conv1d = partial(ConvNd, dim=1)
    Conv2d = partial(ConvNd, dim=2)
    Conv3d = partial(ConvNd, dim=3)

    UpSampling1d = partial(UpSamplingNd)
    UpSampling2d = partial(UpSamplingNd)
    UpSampling3d = partial(UpSamplingNd)

    ConvTransposed1d = partial(ConvNd, dim=1, transposed=True)
    ConvTransposed2d = partial(ConvNd, dim=2, transposed=True)
    ConvTransposed3d = partial(ConvNd, dim=3, transposed=True)

    AvgPool1d = partial(AvgPoolNd, dim=1)
    AvgPool2d = partial(AvgPoolNd, dim=2)
    AvgPool3d = partial(AvgPoolNd, dim=3)

    @staticmethod
    def Parameter(x, dtype=None):
        """A tracked parameter.

        Args:
            x (tensor): Initial value of the parameter.
            dtype (dtype, optional): Data type.

        Returns:
            :class:`torch.nn.Parameter`: Parameter.
        """
        dtype = dtype or torch.float32
        dtype = convert(dtype, B.TorchDType)
        if not isinstance(x, B.TorchNumeric):
            x = torch.tensor(x, dtype=dtype)
        else:
            x = B.cast(dtype, x)
        return torch.nn.Parameter(x, requires_grad=True)


interface = Interface()  #: The PyTorch interface.


class Module(torch.nn.Module):
    """A PyTorch module."""

    def __init__(self):
        super().__init__()
        self.nn = interface
