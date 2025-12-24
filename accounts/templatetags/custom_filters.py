from django import template
import os

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """
    Safely get value from a dictionary by key.
    Usage: {{ dict|get_item:key }}
    """
    if isinstance(dictionary, dict):
        return dictionary.get(key, "")
    return ""

@register.filter
def basename(value):
    """Return just the file name (no path)."""
    if not value:
        return ""
    return os.path.basename(str(value))

from django import template

register = template.Library()

@register.filter
def zip_lists(a, b):
    """Zips two lists together safely for Django templates"""
    return zip(a, b)

from django import template

register = template.Library()

@register.filter
def get_range(start, end):
    """Returns a range from start to end (inclusive)."""
    return range(start, end + 1)