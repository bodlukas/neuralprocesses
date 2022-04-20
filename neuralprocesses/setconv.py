from functools import wraps
from string import ascii_lowercase as letters
from typing import Optional

import lab as B

from . import _dispatch
from .augment import AugmentedInput
from .mask import Masked
from .parallel import broadcast_coder_over_parallel
from .util import register_module, data_dims, batch

__all__ = [
    "SetConv",
    "PrependDensityChannel",
    "DivideByFirstChannel",
    "PrependIdentityChannel",
]


@register_module
class SetConv:
    """A set convolution.

    Args:
        scale (float): Initial value for the length scale.
        dtype (dtype, optional): Data type.

    Attributes:
        log_scale (scalar): Logarithm of the length scale.

    """

    def __init__(self, scale, dtype=None):
        self.log_scale = self.nn.Parameter(B.log(scale), dtype=dtype)


def _dim_is_concrete(x, i):
    try:
        int(B.shape(x, i))
        return True
    except TypeError:
        return False


def _batch_targets(f):
    @wraps(f)
    def f_wrapped(coder, xz, z, x, batch_size=1024, **kw_args):
        # If `x` is the internal discretisation and we're compiling this function
        # with `tf.function`, then `B.shape(x, -1)` will be `None`. We therefore
        # check that `B.shape(x, -1)` is concrete before attempting the comparison.
        if _dim_is_concrete(x, -1) and B.shape(x, -1) > batch_size:
            i = 0
            outs = []
            while i < B.shape(x, -1):
                outs.append(
                    code(
                        coder,
                        xz,
                        z,
                        x[..., i : i + batch_size],
                        batch_size=batch_size,
                        **kw_args,
                    )[1]
                )
                i += batch_size
            return x, B.concat(*outs, axis=-1)
        else:
            return f(coder, xz, z, x, **kw_args)

    return f_wrapped


def compute_weights(coder, x1, x2):
    # Compute interpolation weights.
    dists2 = B.pw_dists2(B.transpose(x1), B.transpose(x2))
    return B.exp(-0.5 * dists2 / B.exp(2 * coder.log_scale))


@_dispatch
@_batch_targets
def code(coder: SetConv, xz: B.Numeric, z: B.Numeric, x: B.Numeric, **kw_args):
    return x, B.matmul(z, compute_weights(coder, xz, x))


@_dispatch
def code(coder: SetConv, xz: B.Numeric, z: B.Numeric, x: tuple, **kw_args):
    ws = [compute_weights(coder, xz[..., i : i + 1, :], xi) for i, xi in enumerate(x)]
    letters_i = 3
    base = "...bc"
    result = "...b"
    for _ in x:
        let = letters[letters_i]
        letters_i += 1
        base += f",...c{let}"
        result += f"{let}"
    return x, B.einsum(f"{base}->{result}", z, *ws)


@_dispatch
@_batch_targets
def code(coder: SetConv, xz: tuple, z: B.Numeric, x: B.Numeric, **kw_args):
    ws = [compute_weights(coder, xzi, x[..., i : i + 1, :]) for i, xzi in enumerate(xz)]
    letters_i = 3
    base_base = "...b"
    base_els = ""
    for _ in xz:
        let = letters[letters_i]
        letters_i += 1
        base_base += f"{let}"
        base_els += f",...{let}c"
    return x, B.einsum(f"{base_base}{base_els}->...bc", z, *ws)


@_dispatch
def code(coder: SetConv, xz: tuple, z: B.Numeric, x: tuple, **kw_args):
    ws = [compute_weights(coder, xzi, xi) for xzi, xi in zip(xz, x)]
    letters_i = 2
    base_base = "...b"
    base_els = ""
    result = "...b"
    for _ in x:
        let1 = letters[letters_i]
        letters_i += 1
        let2 = letters[letters_i]
        letters_i += 1
        base_base += f"{let1}"
        base_els += f",...{let1}{let2}"
        result += f"{let2}"
    return x, B.einsum(f"{base_base}{base_els}->{result}", z, *ws)


broadcast_coder_over_parallel(SetConv)


@_dispatch
def code(coder: SetConv, xz, z, x: AugmentedInput, **kw_args):
    xz, z = code(coder, xz, z, x.x, **kw_args)
    return AugmentedInput(xz, x.augmentation), z


@register_module
class PrependDensityChannel:
    """Prepend a density channel to the current encoding."""


@_dispatch
def code(coder: PrependDensityChannel, xz, z: B.Numeric, x, **kw_args):
    d = data_dims(xz)
    with B.on_device(z):
        density_channel = B.ones(B.dtype(z), *batch(z, d + 1), 1, *B.shape(z)[-d:])
    return xz, B.concat(density_channel, z, axis=-d - 1)


broadcast_coder_over_parallel(PrependDensityChannel)


@_dispatch
def code(coder: PrependDensityChannel, xz, z: Masked, x, **kw_args):
    # Apply mask to density channel _and_ the data channels. Since the mask has
    # only one channel, we can simply pointwise multiply and broadcasting should
    # do the rest for us.
    mask = z.mask
    xz, z = code(coder, xz, z.y, x, **kw_args)
    return xz, (mask * z)


@register_module
class DivideByFirstChannel:
    """Divide by the first channel.

    Args:
        epsilon (float): Value to add to the channel before dividing.

    Attributes:
        epsilon (float): Value to add to the channel before dividing.
    """

    @_dispatch
    def __init__(self, epsilon: float = 1e-8):
        self.epsilon = epsilon


@_dispatch
def code(
    coder: DivideByFirstChannel,
    xz,
    z: B.Numeric,
    x,
    epsilon: Optional[float] = None,
    **kw_args,
):
    epsilon = epsilon or coder.epsilon
    d = data_dims(xz)
    slice_to_one = (Ellipsis, slice(None, 1, None)) + (slice(None, None, None),) * d
    slice_from_one = (Ellipsis, slice(1, None, None)) + (slice(None, None, None),) * d
    return (
        xz,
        B.concat(
            z[slice_to_one],
            z[slice_from_one] / (z[slice_to_one] + epsilon),
            axis=-d - 1,
        ),
    )


broadcast_coder_over_parallel(DivideByFirstChannel)


@register_module
class PrependIdentityChannel:
    """Prepend a density channel to the current encoding."""


@_dispatch
def code(coder: PrependIdentityChannel, xz, z: B.Numeric, x, **kw_args):
    d = data_dims(xz)
    b = batch(z, d + 1)
    with B.on_device(z):
        if d == 2:
            identity_channel = B.diag_construct(B.ones(B.dtype(z), B.shape(z, -1)))
        else:
            raise RuntimeError(
                f"Cannot construct identity channels for encodings of "
                f"dimensionality {d}."
            )
    identity_channel = B.tile(
        B.expand_dims(identity_channel, axis=0, times=len(b) + 1),
        *b,
        1,
        *((1,) * d),
    )
    return xz, B.concat(identity_channel, z, axis=-d - 1)
