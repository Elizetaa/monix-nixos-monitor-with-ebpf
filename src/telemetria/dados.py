"""Contrato comum: ausência é null; erros e fontes acompanham os dados."""


def resultado(source, data=None, *, status=None, error=None):
    return {"source": source, "status": status or ("available" if data is not None else "unavailable"),
            "data": data, "error": error}
