"""CAD-independent tool definitions for new adapters. Existing FreeCAD registry stays compatible."""

import math
from dataclasses import dataclass

from .contract import Result


def validate(value, schema, path="arguments"):
    kind = schema.get("type")
    checks = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
              "string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
              "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v),
              "integer": lambda v: isinstance(v, int) and not isinstance(v, bool), "null": lambda v: v is None}
    kinds = kind if isinstance(kind, list) else [kind]
    if kind and not any(checks[k](value) for k in kinds):
        raise ValueError(f"{path}: beklenen tür {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: geçerli değerler {schema['enum']}")
    for key, sign in (("minimum", 1), ("maximum", -1)):
        if key in schema and sign * value < sign * schema[key]:
            raise ValueError(f"{path}: {key} {schema[key]}")
    if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
        raise ValueError(f"{path}: değer {schema['exclusiveMinimum']} üstünde olmalı")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        if any(k not in value for k in schema.get("required", [])):
            raise ValueError(f"{path}: gerekli alanlar {schema['required']}")
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise ValueError(f"{path}: bilinmeyen alanlar {sorted(set(value) - set(props))}")
        for key, val in value.items():
            if key in props:
                validate(val, props[key], f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 1_000_000):
            raise ValueError(f"{path}: geçersiz öğe sayısı")
        for val in value:
            validate(val, schema.get("items", {}), path + "[]")


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    func: object
    mutates: bool = False
    group: str = "core"

    def manifest(self):
        return {"name": self.name, "title": self.name.replace("_", " "), "description": self.description,
                "input_schema": self.schema, "mutates": self.mutates, "destructive": self.mutates,
                "small": {"description": self.description, "input_schema": self.schema, "group": self.group}}


class Registry:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        self.tools[tool.name] = tool

    def get(self, name):
        return self.tools.get(name)

    def specs(self):
        return list(self.tools.values())

    def normalized_args(self, name, args):
        tool = self.get(name)
        if tool is None:
            raise ValueError(f"Desteklenmeyen araç: {name}")
        validate(args, tool.schema)
        return args

    def run(self, name, args):
        try:
            args = self.normalized_args(name, args)
            return Result(self.get(name).func(**args))
        except Exception as e:
            return Result(f"{type(e).__name__}: {e}", is_error=True)
