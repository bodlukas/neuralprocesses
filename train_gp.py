import argparse

import lab as B
import matplotlib.pyplot as plt
import neuralprocesses.torch as nps
import numpy as np
import stheno
import torch
import wbml.out as out
from neuralprocesses.data import GPGenerator
from wbml.experiment import WorkingDirectory
from wbml.plot import tweak


def with_err(vals):
    """Print the mean value of a list of values with error."""
    vals = B.to_numpy(vals)
    mean = B.mean(vals)
    err = 1.96 * B.std(vals) / B.sqrt(B.length(vals))
    return f"{mean:7.3f} +- {err:7.3f}"


def first_np(x):
    """Get the first batch and convert to NumPy."""
    if B.rank(x) == 2:
        return B.to_numpy(x[0, :])
    elif B.rank(x) == 3:
        return B.to_numpy(x[0, 0, :])
    elif B.rank(x) == 4:
        return B.transpose(B.to_numpy(x[0, :, 0, :]))
    else:
        raise ValueError(f"Rank must be two, three, or four.")


def plot_first_of_batch(batch):
    """Plot the prediction for the first element of a batch."""
    with B.on_device(batch["xt"]):
        x = B.linspace(B.dtype(batch["xt"]), -2, 2, 500)[None, None, :]
        x = B.tile(x, B.shape(batch["xt"], 0), 1, 1)
    pred = run_model(batch["xc"], batch["yc"], x)

    plt.figure(figsize=(6, 4))
    # Plot context and target.
    plt.scatter(
        first_np(batch["xc"]),
        first_np(batch["yc"]),
        label="Context",
        style="train",
        s=20,
    )
    plt.scatter(
        first_np(batch["xt"]),
        first_np(batch["yt"]),
        label="Target",
        style="test",
        s=20,
    )
    # Plot prediction.
    err = 1.96 * B.sqrt(pred.var)
    plt.plot(
        first_np(x),
        first_np(pred.mean),
        label="Prediction",
        style="pred",
    )
    plt.fill_between(
        first_np(x),
        first_np(pred.mean - err),
        first_np(pred.mean + err),
        style="pred",
    )
    plt.plot(
        first_np(x),
        first_np(pred.sample(5)),
        style="pred",
        ls="-",
        lw=0.5,
    )
    # Plot prediction by ground truth.
    f = stheno.GP(kernel)
    # Make sure that everything is of `float64`s and on the GPU.
    noise = B.to_active_device(B.cast(torch.float64, gen_eval.noise))
    xc = B.transpose(B.cast(torch.float64, batch["xc"]))
    yc = B.transpose(B.cast(torch.float64, batch["yc"]))
    x = B.transpose(B.cast(torch.float64, x))
    # Compute posterior GP.
    f_post = f | (f(xc, noise), yc)
    mean, lower, upper = f_post(x).marginal_credible_bounds()
    plt.plot(
        first_np(B.transpose(x)),
        first_np(mean),
        label="Truth",
        style="pred2",
    )
    plt.plot(
        first_np(B.transpose(x)),
        first_np(lower),
        style="pred2",
    )
    plt.plot(
        first_np(B.transpose(x)),
        first_np(upper),
        style="pred2",
    )
    plt.xlim(B.min(x), B.max(x))
    tweak()
    plt.savefig(wd.file(f"epoch-{i:03d}.pdf"))
    plt.close()


# Setup arguments.
parser = argparse.ArgumentParser()
parser.add_argument("--dim_x", type=int, default=1)
parser.add_argument("--dim_y", type=int, default=1)
parser.add_argument("--epochs", type=int, default=100)
parser.add_argument("--batch_size", type=int, default=16)
parser.add_argument("--rate", type=float, default=1e-4)
parser.add_argument("--margin", type=float, default=2)
parser.add_argument("--arch", type=str)
parser.add_argument("--learnable_channel", action="store_true")
parser.add_argument("--subdir", type=str, nargs="*")
parser.add_argument(
    "--model",
    choices=["cnp", "convcnp", "convgnp-linear"],
    required=True,
)
parser.add_argument(
    "--data",
    choices=["eq", "matern", "weakly-periodic"],
    default="eq",
)
args = parser.parse_args()

# Ensure that the `arch` is specified when it is required.
models_which_require_arch = {"convcnp", "convgnp-linear"}
if args.model in models_which_require_arch and not args.arch:
    raise RuntimeError(f"Model requires a choice of architecture. Please set `--arch`.")

# Setup script.
out.report_time = True
if args.learnable_channel:
    suffix = "_lc"
else:
    suffix = ""
wd = WorkingDirectory(
    "_experiments",
    *(args.subdir or ()),
    f"{args.model}",
    *((args.arch,) if args.arch else ()),
    f"x{args.dim_x}_y{args.dim_y}{suffix}",
)

# Setup data.
noise = 0.05
if args.data == "eq":
    kernel = stheno.EQ().stretch(0.25)
elif args.data == "matern":
    kernel = stheno.Matern().stretch(0.25)
elif args.data == "weakly-periodic":
    kernel = stheno.EQ().stretch(0.5) * stheno.EQ().periodic(period=0.25)
else:
    raise RuntimeError(f'Unknown data set "{args.data}".')

# Setup architecture.
if args.dim_x == 1:
    unet_channels = (64,) * 6
    dws_channels = 128
    dws_receptive_field = 2
    points_per_unit = 64
elif args.dim_x == 2:
    kernel = EQ().stretch(0.25)
    unet_channels = (64,) * 6
    dws_channels = 128
    dws_receptive_field = 2
    # Need to reduce the PPU to reduce memory consumption.
    points_per_unit = 64 / 2
else:
    raise RuntimeError("Could not determine kernel for input dimensionality.")

# Use a GPU if one is available.
if torch.cuda.is_available():
    device = "cuda"
else:
    device = "cpu"
B.set_global_device(device)

# Construct the model.
if args.model == "convcnp":
    model = nps.construct_convgnp(
        points_per_unit=points_per_unit,
        dim_x=args.dim_x,
        dim_y=args.dim_y,
        likelihood="het",
        conv_arch=args.arch,
        unet_channels=unet_channels,
        dws_channels=dws_channels,
        dws_receptive_field=dws_receptive_field,
        margin=args.margin,
    )
    run_model = model
elif args.model == "cnp":
    model = nps.construct_gnp(
        dim_x=args.dim_x,
        dim_y=args.dim_y,
        likelihood="het",
    )
    run_model = model
elif args.model == "convgnp-linear":
    if args.learnable_channel:
        model = nps.construct_convgnp(
            points_per_unit=points_per_unit,
            dim_x=args.dim_x,
            dim_y=args.dim_y,
            dim_yc=(args.dim_y, 128),
            likelihood="lowrank",
            conv_arch=args.arch,
            unet_channels=unet_channels,
            dws_channels=dws_channels,
            dws_receptive_field=dws_receptive_field,
            num_basis_functions=512,
            margin=args.margin,
        )

        with B.on_device(device):
            x_lc = B.linspace(torch.float32, -2, 2, 256 + 1)[None, None, :]
            x_lc = B.tile(x_lc, args.batch_size, 1, 1)
            y_lc = B.randn(torch.float32, args.batch_size, 128, 256 + 1)
        model.y_lc = model.nn.Parameter(y_lc)

        def run_model(xc, yc, xt):
            return model([(xc, yc), (x_lc, model.y_lc)], xt)

    else:

        model = nps.construct_convgnp(
            points_per_unit=points_per_unit,
            dim_x=args.dim_x,
            dim_y=args.dim_y,
            likelihood="lowrank",
            conv_arch=args.arch,
            unet_channels=unet_channels,
            dws_channels=dws_channels,
            dws_receptive_field=dws_receptive_field,
            num_basis_functions=512,
            margin=args.margin,
        )
        run_model = model

else:
    raise ValueError(f'Invalid model "{args.model}".')

# Ensure that the model is on the GPU.
model = model.to(device)

# Print the model's number of parameters.
out.kv("Number of parameters", nps.num_params(model))

# Set up the training and validation data generators.
gen = GPGenerator(
    torch.float32,
    kernel=kernel,
    noise=noise,
    batch_size=args.batch_size,
    num_context_points=(3, 20),
    num_target_points=50,
    x_ranges=((-2, 2),) * args.dim_x,
    dim_y=args.dim_y,
    pred_logpdf=False,
    pred_logpdf_diag=False,
    device=device,
)
gen_eval = GPGenerator(
    torch.float32,
    kernel=kernel,
    noise=noise,
    num_tasks=2**12,
    batch_size=args.batch_size,
    num_context_points=(3, 20),
    num_target_points=50,
    x_ranges=((-2, 2),) * args.dim_x,
    dim_y=args.dim_y,
    pred_logpdf=True,
    pred_logpdf_diag=True,
    device=device,
)


def objective(xc, yc, xt, yt):
    """Objective function."""
    pred = run_model(xc, yc, xt)
    # Use `float64`s for the logpdf computation.
    pred = B.cast(torch.float64, pred)
    return -pred.logpdf(B.cast(torch.float64, yt))


# Setup training loop.
opt = torch.optim.Adam(model.parameters(), args.rate)
best_eval_loss = np.inf

for i in range(args.epochs):
    with out.Section(f"Epoch {i + 1}"):
        # Perform an epoch.
        for batch in gen.epoch():
            val = B.mean(
                objective(
                    batch["xc"],
                    batch["yc"],
                    batch["xt"],
                    batch["yt"],
                )
            )
            opt.zero_grad(set_to_none=True)
            val.backward()
            opt.step()

        # Save current model.
        torch.save(model.state_dict(), wd.file(f"model-last.torch"))

        # The epoch is done. Now evaluate.
        with torch.no_grad():
            vals = []
            full_vals = []
            diag_vals = []
            for batch in gen_eval.epoch():
                vs = objective(
                    batch["xc"],
                    batch["yc"],
                    batch["xt"],
                    batch["yt"],
                )
                nt = B.shape(batch["xt"], 2)
                vals.append(B.to_numpy(vs) / nt)
                full_vals.append(B.to_numpy(vs + batch["pred_logpdf"]) / nt)
                diag_vals.append(B.to_numpy(vs + batch["pred_logpdf_diag"]) / nt)
            vals = B.concat(*vals)
            full_vals = B.concat(*full_vals)
            diag_vals = B.concat(*diag_vals)
            out.kv("Loglik", with_err(-vals))
            out.kv("KL (diag)", with_err(diag_vals))
            out.kv("KL (full)", with_err(full_vals))

        # Check if the model is the new best. If so, save it.
        if B.mean(vals) < best_eval_loss:
            out.out("New best model!")
            best_eval_loss = B.mean(vals)
            torch.save(model.state_dict(), wd.file(f"model-best.torch"))

        # Visualise a prediction by the model.
        if args.dim_x == 1 and args.dim_y == 1:
            with torch.no_grad():
                plot_first_of_batch(gen_eval.generate_batch())
