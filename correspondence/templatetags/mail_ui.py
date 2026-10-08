import json
from pathlib import Path
from django import template
register=template.Library()
TRANSLATIONS=json.loads((Path(__file__).resolve().parent.parent/'translations.json').read_text())
@register.simple_tag(takes_context=True)
def ui(context,text):
    request=context.get('request')
    if request and request.session.get('ui_language')=='en':return TRANSLATIONS.get(text,text)
    return text
