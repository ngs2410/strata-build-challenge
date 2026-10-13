import pytest

from src.prompt_registry import PROMPT_REGISTRY, PromptEntry, resolve


def test_registry_has_four_tasks():
  assert set(PROMPT_REGISTRY) == {
    'version_diff',
    'citation_verify',
    'obligation_extraction',
    'mapping',
  }


def test_registry_entries_name_task_config_keys_matching_registry_keys():
  for task_name, entry in PROMPT_REGISTRY.items():
    assert entry.task_config_key == task_name


def test_resolve_imports_module_and_returns_attribute():
  entry = PROMPT_REGISTRY['version_diff']
  builder = resolve(entry.builder_module, entry.builder_function)
  assert callable(builder)
  stub = resolve(entry.stub_module, entry.stub_function)
  assert callable(stub)


def test_resolve_raises_on_missing_function():
  with pytest.raises(AttributeError):
    resolve('src.version_diff', 'does_not_exist')


def test_resolve_raises_on_missing_module():
  with pytest.raises(ModuleNotFoundError):
    resolve('src.imaginary_module', 'anything')


def test_prompt_entry_is_frozen():
  import dataclasses

  entry = PromptEntry(
    builder_module='m',
    builder_function='f',
    response_module='m',
    response_class='C',
    stub_module='m',
    stub_function='s',
    task_config_key='k',
  )
  with pytest.raises(dataclasses.FrozenInstanceError):
    entry.builder_module = 'x'
