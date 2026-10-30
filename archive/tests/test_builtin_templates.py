from evalplat.models.test_template import TestTemplate as TemplateModel, TestTemplateVersion as TemplateVersionModel
from evalplat.services.template_service import BUILTIN_TEMPLATES, ensure_builtin_templates


def test_builtin_templates_are_seeded_idempotently(db):
    first = ensure_builtin_templates(db)
    second = ensure_builtin_templates(db)

    assert first == len(BUILTIN_TEMPLATES)
    assert second == 0
    for definition in BUILTIN_TEMPLATES:
        template = next(item for item in db.query(TemplateModel).all() if item.name == definition["name"])
        version = db.query(TemplateVersionModel).filter(TemplateVersionModel.template_id == template.id).one()
        assert template.is_builtin is True
        assert version.test_type == definition["test_type"]
