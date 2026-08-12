import os
import pytest
from pathlib import Path

from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.prompt_metadata import PromptMetadata, PromptType
from app.services.ai.FieldOpsAI.runtime.prompt_registry import (
    PromptTemplateRegistry,
    PromptRegistrationError,
    PromptNotFoundError,
    PromptFileError,
    get_default_prompt_registry,
)

@pytest.fixture
def temp_prompt_root(tmp_path):
    root = tmp_path / "prompts"
    root.mkdir()
    return root

@pytest.fixture
def registry(temp_prompt_root):
    return PromptTemplateRegistry(prompt_root=temp_prompt_root, max_prompt_bytes=1024)

def test_register_and_retrieve_system_prompt(registry, temp_prompt_root):
    file_path = temp_prompt_root / "sys1.md"
    file_path.write_text("system instruction 1", encoding="utf-8")
    
    meta = PromptMetadata(key="SYS1", relative_path="sys1.md", prompt_type=PromptType.SYSTEM)
    registry.register(meta)
    
    assert registry.get_text("SYS1") == "system instruction 1"
    assert registry.get_system_instructions() == ("system instruction 1",)

def test_register_and_retrieve_task_prompt(registry, temp_prompt_root):
    file_path = temp_prompt_root / "task1.md"
    file_path.write_text("task instruction", encoding="utf-8")
    
    meta = PromptMetadata(key="TASK1", relative_path="task1.md", prompt_type=PromptType.TASK, task=AITask.PLANNING)
    registry.register(meta)
    
    assert registry.get_text("TASK1") == "task instruction"
    assert registry.get_task_prompt(AITask.PLANNING) == "task instruction"

def test_preserve_deterministic_system_order(registry, temp_prompt_root):
    (temp_prompt_root / "sys1.md").write_text("1", encoding="utf-8")
    (temp_prompt_root / "sys2.md").write_text("2", encoding="utf-8")
    
    registry.register(PromptMetadata(key="S2", relative_path="sys2.md", prompt_type=PromptType.SYSTEM))
    registry.register(PromptMetadata(key="S1", relative_path="sys1.md", prompt_type=PromptType.SYSTEM))
    
    assert registry.get_system_instructions() == ("2", "1")

def test_reject_duplicate_keys(registry, temp_prompt_root):
    (temp_prompt_root / "test.md").write_text("test", encoding="utf-8")
    meta = PromptMetadata(key="DUP", relative_path="test.md", prompt_type=PromptType.SYSTEM)
    registry.register(meta)
    
    with pytest.raises(PromptRegistrationError, match="Duplicate prompt key"):
        registry.register(meta)

def test_reject_duplicate_task(registry, temp_prompt_root):
    (temp_prompt_root / "test.md").write_text("test", encoding="utf-8")
    meta1 = PromptMetadata(key="T1", relative_path="test.md", prompt_type=PromptType.TASK, task=AITask.PLANNING)
    meta2 = PromptMetadata(key="T2", relative_path="test.md", prompt_type=PromptType.TASK, task=AITask.PLANNING)
    
    registry.register(meta1)
    with pytest.raises(PromptRegistrationError, match="Duplicate task registration"):
        registry.register(meta2)

def test_reject_unknown_key(registry):
    with pytest.raises(PromptNotFoundError, match="Unknown prompt key"):
        registry.get_text("UNKNOWN")

def test_reject_unregistered_task(registry):
    with pytest.raises(PromptNotFoundError, match="Task not registered"):
        registry.get_task_prompt(AITask.PLANNING)

def test_reject_blank_keys(registry):
    with pytest.raises(PromptNotFoundError, match="Blank prompt key"):
        registry.get_text("   ")

def test_reject_absolute_paths():
    with pytest.raises(ValueError, match="relative_path must be relative"):
        PromptMetadata(key="A", relative_path="/tmp/test.md", prompt_type=PromptType.SYSTEM)

def test_reject_dot_dot_traversal():
    with pytest.raises(ValueError, match="relative_path must not contain traversal"):
        PromptMetadata(key="A", relative_path="../test.md", prompt_type=PromptType.SYSTEM)

def test_reject_symlink_escape(registry, temp_prompt_root, tmp_path):
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")
    
    symlink_path = temp_prompt_root / "link.md"
    try:
        os.symlink(outside_file, symlink_path)
    except OSError:
        pytest.skip("Symlinks not supported by OS")
        
    meta = PromptMetadata(key="SYM", relative_path="link.md", prompt_type=PromptType.SYSTEM)
    with pytest.raises(PromptFileError, match="Path traversal detected"):
        registry.register(meta)

def test_reject_missing_file(registry):
    meta = PromptMetadata(key="MISSING", relative_path="missing.md", prompt_type=PromptType.SYSTEM)
    with pytest.raises(PromptFileError, match="Prompt file does not exist"):
        registry.register(meta)

def test_reject_directory(registry, temp_prompt_root):
    (temp_prompt_root / "dir.md").mkdir()
    meta = PromptMetadata(key="DIR", relative_path="dir.md", prompt_type=PromptType.SYSTEM)
    with pytest.raises(PromptFileError, match="Prompt path is not a regular file"):
        registry.register(meta)

def test_reject_empty_or_whitespace_file(registry, temp_prompt_root):
    (temp_prompt_root / "empty.md").write_text("", encoding="utf-8")
    meta = PromptMetadata(key="E", relative_path="empty.md", prompt_type=PromptType.SYSTEM)
    with pytest.raises(PromptFileError, match="Prompt file is empty"):
        registry.register(meta)
        
    (temp_prompt_root / "white.md").write_text("   \n  ", encoding="utf-8")
    meta2 = PromptMetadata(key="W", relative_path="white.md", prompt_type=PromptType.SYSTEM)
    with pytest.raises(PromptFileError, match="empty or whitespace-only"):
        registry.register(meta2)

def test_reject_oversized_file(registry, temp_prompt_root):
    (temp_prompt_root / "big.md").write_text("A" * 2000, encoding="utf-8")
    meta = PromptMetadata(key="BIG", relative_path="big.md", prompt_type=PromptType.SYSTEM)
    with pytest.raises(PromptFileError, match="exceeds maximum allowed size"):
        registry.register(meta)

def test_reject_invalid_utf8(registry, temp_prompt_root):
    (temp_prompt_root / "bad.md").write_bytes(b"\xff\xfe\xfd")
    meta = PromptMetadata(key="BAD", relative_path="bad.md", prompt_type=PromptType.SYSTEM)
    with pytest.raises(PromptFileError, match="not valid UTF-8"):
        registry.register(meta)

def test_no_absolute_path_in_errors(registry):
    meta = PromptMetadata(key="MISSING", relative_path="missing.md", prompt_type=PromptType.SYSTEM)
    try:
        registry.register(meta)
    except PromptFileError as e:
        assert str(registry._prompt_root) not in str(e)

def test_list_registered_immutable(registry, temp_prompt_root):
    (temp_prompt_root / "test.md").write_text("test", encoding="utf-8")
    registry.register(PromptMetadata(key="B", relative_path="test.md", prompt_type=PromptType.SYSTEM))
    registry.register(PromptMetadata(key="A", relative_path="test.md", prompt_type=PromptType.SYSTEM))
    
    lst = registry.list_registered()
    assert isinstance(lst, tuple)
    assert len(lst) == 2
    assert lst[0].key == "A"
    assert lst[1].key == "B"

def test_get_system_instructions_returns_tuple(registry, temp_prompt_root):
    (temp_prompt_root / "test.md").write_text("test", encoding="utf-8")
    registry.register(PromptMetadata(key="A", relative_path="test.md", prompt_type=PromptType.SYSTEM))
    
    inst = registry.get_system_instructions()
    assert isinstance(inst, tuple)
    assert inst == ("test",)

def test_cache_usage(registry, temp_prompt_root):
    file_path = temp_prompt_root / "test.md"
    file_path.write_text("test", encoding="utf-8")
    meta = PromptMetadata(key="A", relative_path="test.md", prompt_type=PromptType.SYSTEM)
    registry.register(meta)
    
    assert registry.get_text("A") == "test"
    file_path.write_text("changed", encoding="utf-8")
    # Second read should be cached
    assert registry.get_text("A") == "test"

def test_clear_cache(registry, temp_prompt_root):
    file_path = temp_prompt_root / "test.md"
    file_path.write_text("test", encoding="utf-8")
    meta = PromptMetadata(key="A", relative_path="test.md", prompt_type=PromptType.SYSTEM)
    registry.register(meta)
    
    assert registry.get_text("A") == "test"
    file_path.write_text("changed", encoding="utf-8")
    registry.clear_cache()
    # Should re-read
    assert registry.get_text("A") == "changed"

def test_disabled_definitions(registry, temp_prompt_root):
    (temp_prompt_root / "test.md").write_text("test", encoding="utf-8")
    registry.register(PromptMetadata(key="A", relative_path="test.md", prompt_type=PromptType.SYSTEM, enabled=False))
    
    assert registry.get_system_instructions() == ()
    assert registry.get_text("A") == "test"  # Direct lookup still works

def test_default_registry():
    r1 = get_default_prompt_registry()
    r2 = get_default_prompt_registry()
    assert r1 is r2
    
    lst = r1.list_registered()
    assert len(lst) >= 12

from unittest.mock import patch, MagicMock
from app.services.ai.FieldOpsAI.runtime.prompt_builder import PromptBuilder
from app.services.ai.FieldOpsAI.runtime.orchestrator import AIOrchestrator
from app.services.ai.FieldOpsAI.runtime.prompt_registry import PromptRegistryError

def test_injected_registry_usage(temp_prompt_root):
    (temp_prompt_root / "sys.md").write_text("injected_sys", encoding="utf-8")
    (temp_prompt_root / "task.md").write_text("injected_task", encoding="utf-8")
    
    injected_registry = PromptTemplateRegistry(prompt_root=temp_prompt_root)
    injected_registry.register(PromptMetadata(key="SYS", relative_path="sys.md", prompt_type=PromptType.SYSTEM))
    injected_registry.register(PromptMetadata(key="TASK", relative_path="task.md", prompt_type=PromptType.TASK, task=AITask.PLANNING))
    
    builder = PromptBuilder(registry=injected_registry)
    
    with patch("app.services.ai.FieldOpsAI.runtime.prompt_builder.get_default_prompt_registry") as mock_get_default:
        mock_get_default.side_effect = RuntimeError("Should not be called")
        
        # Test Builder
        assert builder.build() == "injected_sys"
        assert builder.get_task_prompt(AITask.PLANNING) == "injected_task"
        
        # Test Orchestrator
        orchestrator = AIOrchestrator(prompt_builder=builder)
        assert orchestrator._load_task_prompt(AITask.PLANNING) == "injected_task"

def test_prompt_builder_lazy_init():
    with patch("app.services.ai.FieldOpsAI.runtime.prompt_builder.get_default_prompt_registry") as mock_get_default:
        mock_get_default.return_value = MagicMock()
        mock_get_default.return_value.get_system_instructions.return_value = ("test",)
        
        builder = PromptBuilder()
        mock_get_default.assert_not_called()
        
        builder.build()
        mock_get_default.assert_called_once()
        
        builder.build()
        assert mock_get_default.call_count == 1

def test_metadata_normalization():
    m1 = PromptMetadata(key="  whitespace  ", relative_path="a.md", prompt_type=PromptType.SYSTEM)
    assert m1.key == "WHITESPACE"
    
    m2 = PromptMetadata(key=" identity ", relative_path="a.md", prompt_type=PromptType.SYSTEM)
    assert m2.key == "IDENTITY"
    
    with pytest.raises(ValueError, match="key must not be blank"):
        PromptMetadata(key="   ", relative_path="a.md", prompt_type=PromptType.SYSTEM)
        
    m3 = PromptMetadata(key="HARMLESS", relative_path="rules..backup.md", prompt_type=PromptType.SYSTEM)
    assert m3.relative_path == "rules..backup.md"

def test_invalid_task_types(registry):
    with pytest.raises(PromptNotFoundError, match="Invalid task type"):
        registry.get_task_prompt("PLANNING")
        
    with pytest.raises(PromptNotFoundError, match="Invalid task type"):
        registry.get_task_prompt(1)

def test_constructor_validation(tmp_path):
    with pytest.raises(PromptRegistryError, match="not a directory|does not exist"):
        PromptTemplateRegistry(prompt_root=tmp_path / "nonexistent")
        
    with pytest.raises(PromptRegistryError, match="max_prompt_bytes must be a positive integer"):
        PromptTemplateRegistry(prompt_root=tmp_path, max_prompt_bytes=0)
        
    with pytest.raises(PromptRegistryError, match="max_prompt_bytes must be a positive integer"):
        PromptTemplateRegistry(prompt_root=tmp_path, max_prompt_bytes=True)

def test_legacy_prompt_builder_output():
    # Load independently
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app" / "services" / "ai" / "FieldOpsAI"
    
    files = [
        "IDENTITY.md",
        "SOUL.md",
        "knowledge/business_rules.md",
        "knowledge/lifecycle.md",
        "knowledge/roles.md",
        "knowledge/validation.md"
    ]
    
    contents = []
    for f in files:
        p = root / f
        if p.exists():
            contents.append(p.read_text(encoding="utf-8").strip())
            
    expected = "\n\n".join(contents)
    
    builder = PromptBuilder()
    actual = builder.build()
    
    # Byte for byte
    assert actual.encode('utf-8') == expected.encode('utf-8')
