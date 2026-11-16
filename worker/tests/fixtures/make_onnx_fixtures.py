"""Genera los modelos ONNX diminutos que usan los tests del clasificador (se versionan, pesan ~KB).

Necesita el paquete `onnx`, que no es dependencia del worker. En Windows con un runtime de Visual C++
antiguo `onnx` no carga, así que se ejecuta en Docker:

    docker run --rm -v "$PWD/worker/tests/fixtures:/out" python:3.11-slim \
        sh -c 'pip install -q "onnx==1.17.0" "numpy<2" && python /out/make_onnx_fixtures.py /out'

Las salidas son constantes conocidas (no dependen de la entrada) para que los tests puedan comprobar
exactamente cómo el worker interpreta cada formato de salida.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

OPSET = 13  # ReduceMean con `axes` como atributo; compatible con onnxruntime >= 1.10
IR_VERSION = 8

SEQ_LEN = 8
FEATURES = 99
POSE_LOGITS = np.array([[3.0, 0.0, 0.0]], dtype=np.float32)  # forehand, backhand, other

IMGSZ = 32
ANCHORS = 5
# YOLO: filas 0-3 = caja; 4 = forehand; 5 = serve. Máximo por clase: 0.8 y 0.3.
YOLO_OUTPUT = np.zeros((1, 6, ANCHORS), dtype=np.float32)
YOLO_OUTPUT[0, 4] = [0.1, 0.8, 0.2, 0.0, 0.5]
YOLO_OUTPUT[0, 5] = [0.3, 0.1, 0.0, 0.2, 0.1]


def constant_model(input_name: str, input_shape: list[int], output: np.ndarray, axes: list[int]) -> onnx.ModelProto:
    """salida = constante + 0 * media(entrada): depende formalmente de la entrada y es determinista."""
    nodes = [
        helper.make_node("ReduceMean", [input_name], ["mean"], axes=axes, keepdims=0),
        helper.make_node("Mul", ["mean", "zero"], ["zeroed"]),
        helper.make_node("Add", ["zeroed", "constant"], ["output"]),
    ]
    graph = helper.make_graph(
        nodes, "fixture",
        [helper.make_tensor_value_info(input_name, TensorProto.FLOAT, input_shape)],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, list(output.shape))],
        initializer=[numpy_helper.from_array(np.zeros(1, dtype=np.float32), "zero"),
                     numpy_helper.from_array(output, "constant")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", OPSET)])
    model.ir_version = IR_VERSION
    onnx.checker.check_model(model)
    return model


def main(out: Path) -> None:
    pose = constant_model("poses", [1, SEQ_LEN, FEATURES], POSE_LOGITS, axes=[1, 2])
    onnx.save(pose, out / "pose_seq.onnx")
    yolo = constant_model("images", [1, 3, IMGSZ, IMGSZ], YOLO_OUTPUT, axes=[1, 2, 3])
    onnx.save(yolo, out / "yolo.onnx")
    print("fixtures escritos en", out)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
