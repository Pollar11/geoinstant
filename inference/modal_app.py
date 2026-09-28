"""Modal GPU deployment: `modal deploy modal_app.py`."""

import modal

image = (
    modal.Image.from_registry("nvcr.io/nvidia/tensorrt:24.08-py3", add_python="3.11")
    .pip_install_from_pyproject("pyproject.toml")
    .pip_install("onnxruntime-gpu>=1.19", "rapidocr_onnxruntime>=1.3")
    .env(
        {
            "GEOINSTANT_ARTIFACTS_DIR": "/artifacts",
            "GEOINSTANT_FEEDBACK_DIR": "/feedback",
            "ORT_TENSORRT_ENGINE_CACHE_ENABLE": "1",
            "ORT_TENSORRT_CACHE_PATH": "/artifacts/trt-cache",
            "ORT_TENSORRT_FP16_ENABLE": "1",
        }
    )
    .add_local_python_source("geoinstant")
)
artifacts = modal.Volume.from_name("geoinstant-artifacts", create_if_missing=True)
feedback = modal.Volume.from_name("geoinstant-feedback", create_if_missing=True)
app = modal.App("geoinstant", image=image)


@app.function(
    gpu="L4",
    volumes={"/artifacts": artifacts, "/feedback": feedback},
    secrets=[modal.Secret.from_name("anthropic")],
    min_containers=1,
    max_containers=20,
    scaledown_window=300,
    timeout=60,
)
@modal.concurrent(max_inputs=16)
@modal.asgi_app()
def serve():  # type: ignore[no-untyped-def]
    from geoinstant.main import create_app

    return create_app()
