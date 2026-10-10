"""Runtime document identities, including close/reopen of documents with the same name."""

import secrets

_documents = {}


def document_context():
    import FreeCAD

    from . import ui_actions

    alive = FreeCAD.listDocuments()
    for name in set(_documents) - set(alive):
        del _documents[name]
    doc = FreeCAD.ActiveDocument
    identity = None
    if doc is not None:
        entry = _documents.get(doc.Name)
        if entry is None or entry[0] is not doc:
            entry = _documents[doc.Name] = (doc, secrets.token_hex(16))
        identity = entry[1]
    return {"document_id": identity, "document_name": doc.Name if doc else None, "revision": ui_actions.STATE["doc"]}
