from __future__ import annotations

import ast
import importlib.util

from pathlib import Path
from types import ModuleType
from typing import Callable

from dashboard.scenario_loader import safe_resolve

class FunctionLoadError(RuntimeError):
    pass

DEFAULT_ALLOWED_IMPORTS = {"pandas", "numpy", "math", "statistics"}

def validate_allowed_imports(file_path: Path, allowed_imports: list[str] | None = None) -> None:
    """Lightweight static check for imports in scenario-local functions.py.

    This is not a full security sandbox. It prevents accidental use of obviously risky imports
    and supports the thesis prototype's goal of reducing risk without overengineering.
    """
    allowed = set(allowed_imports or DEFAULT_ALLOWED_IMPORTS) | {"__future__"}
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in allowed:
                    raise FunctionLoadError(f"Import '{root}' is not allowed in {file_path.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root and root not in allowed:
                raise FunctionLoadError(f"Import '{root}' is not allowed in {file_path.name}")
            
def load_module_from_file(file_path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise FunctionLoadError(f"Could not create import spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def load_approved_function(scenario_folder: Path, approved_function_decl: dict) -> Callable:
    """Load only explicitly declared scenario-local functions.

    Expected declaration:
    {
      "id": "compute_school_closure_metrics",
      "file": "functions.py",
      "callable": "compute_school_closure_metrics",
      "allowed_imports": ["pandas", "numpy", "math"]
    }
    """
    rel_file = approved_function_decl.get("file")
    callable_name = approved_function_decl.get("callable")
    if not rel_file or not callable_name:
        raise FunctionLoadError("Approved function declaration must include file and callable")

    file_path = safe_resolve(scenario_folder, rel_file)
    if file_path is None or not file_path.exists():
        raise FunctionLoadError(f"Approved function file not found: {rel_file}")
    if file_path.suffix != ".py":
        raise FunctionLoadError("Approved function files must be Python .py files")

    validate_allowed_imports(file_path, approved_function_decl.get("allowed_imports"))

    module_name = f"scenario_functions_{scenario_folder.name}_{callable_name}"
    module = load_module_from_file(file_path, module_name)
    func = getattr(module, callable_name, None)
    if func is None or not callable(func):
        raise FunctionLoadError(f"Callable '{callable_name}' not found in {file_path.name}")
    return func


def get_approved_function_decl(preprocessing_config: dict, function_ref: str) -> dict:
    for decl in preprocessing_config.get("approved_functions", []):
        if decl.get("id") == function_ref:
            return decl
    raise FunctionLoadError(f"Function ref '{function_ref}' is not declared in approved_functions")