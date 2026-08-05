import sys
import types
import importlib
import pytest
from pathlib import Path
import importlib.util


class DummyLogger:
    def debug(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class DummySession:
    def __init__(self):
        self.record = None

    def exec(self, *a, **k):
        record = self.record

        class R:
            def first(self):
                return record

            def yield_per(self, n):
                if record is None:
                    return iter(())
                return iter((record,))

        return R()

    def add(self, record, *a, **k):
        self.record = record

    def flush(self, *a, **k):
        pass

    def delete(self, *a, **k):
        self.record = None

    def expunge(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class DummyMaiImage:
    def __init__(self, full_path=None, image_bytes=None):
        self.full_path = full_path
        self.image_bytes = image_bytes
        self.file_hash = "dummy-hash"
        self.image_format = "png"
        self.description = ""
        self.vlm_processed = False

    @classmethod
    def from_db_instance(cls, record):
        image = cls(full_path=getattr(record, "full_path", None))
        image.file_hash = getattr(record, "image_hash", "dummy-hash")
        image.description = getattr(record, "description", "")
        image.vlm_processed = getattr(record, "vlm_processed", False)
        return image

    def to_db_instance(self):
        return types.SimpleNamespace(
            description=self.description,
            full_path=str(self.full_path) if self.full_path is not None else "",
            id=1,
            image_hash=self.file_hash,
            image_type="image",
            last_used_time=None,
            no_file_flag=False,
            query_count=0,
            register_time=None,
            vlm_processed=self.vlm_processed,
        )

    async def calculate_hash_format(self):
        self.file_hash = "dummy-hash"
        return None


class DummyLLMRequest:
    def __init__(self, *a, **k):
        pass

    async def generate_response_for_image(self, prompt, image_base64, image_format, temp):
        return ("dummy description", {})


class DummyLLMServiceClient:
    def __init__(self, *a, **k):
        pass

    async def generate_response_for_image(self, prompt, image_base64, image_format, options=None):
        return types.SimpleNamespace(response="dummy description")


class DummySelect:
    def __init__(self, *a, **k):
        pass

    def filter_by(self, *a, **k):
        return self

    def limit(self, n):
        return self


class DetachedRecord:
    def __init__(self, description="cached description", vlm_processed=True):
        self._detached = False
        self._description = description
        self._vlm_processed = vlm_processed

    @property
    def description(self):
        if not self._detached:
            raise RuntimeError("attribute refresh operation cannot proceed")
        return self._description

    @property
    def vlm_processed(self):
        if not self._detached:
            raise RuntimeError("attribute refresh operation cannot proceed")
        return self._vlm_processed


class DetachedRecordSession(DummySession):
    def __init__(self, record):
        self.record = record

    def exec(self, *a, **k):
        record = self.record

        class R:
            def first(self):
                return record

        return R()

    def expunge(self, record):
        record._detached = True


@pytest.fixture(autouse=True)
def patch_external_dependencies(monkeypatch):
    # Provide dummy implementations as modules so that importing image_manager is safe
    # Patch LLMRequest
    llm_mod = types.SimpleNamespace(LLMRequest=DummyLLMRequest)
    monkeypatch.setitem(sys.modules, "src.llm_models.utils_model", llm_mod)
    llm_service_mod = types.SimpleNamespace(LLMServiceClient=DummyLLMServiceClient)
    monkeypatch.setitem(sys.modules, "src.services.llm_service", llm_service_mod)

    # Patch logger
    logger_mod = types.SimpleNamespace(get_logger=lambda name: DummyLogger())
    monkeypatch.setitem(sys.modules, "src.common.logger", logger_mod)

    # Patch DB session provider
    shared_session = DummySession()
    db_mod = types.SimpleNamespace(get_db_session=lambda: shared_session)
    monkeypatch.setitem(sys.modules, "src.common.database.database", db_mod)

    # Patch database model types
    db_model_mod = types.SimpleNamespace(Images=types.SimpleNamespace, ImageType=types.SimpleNamespace(IMAGE="image"))
    monkeypatch.setitem(sys.modules, "src.common.database.database_model", db_model_mod)

    # Patch MaiImage data model
    data_model_mod = types.SimpleNamespace(MaiImage=DummyMaiImage)
    monkeypatch.setitem(sys.modules, "src.common.data_models.image_data_model", data_model_mod)

    # Patch SQLModel select function
    sql_mod = types.SimpleNamespace(select=lambda *a, **k: DummySelect())
    monkeypatch.setitem(sys.modules, "sqlmodel", sql_mod)

    # Patch prompt manager used to build image description prompt.
    class _PromptManager:
        def get_prompt(self, _name):
            return types.SimpleNamespace()

        async def render_prompt(self, _prompt):
            return "test-style"

    prompt_manager_mod = types.SimpleNamespace(prompt_manager=_PromptManager())
    monkeypatch.setitem(sys.modules, "src.prompt.prompt_manager", prompt_manager_mod)

    llm_options_mod = types.SimpleNamespace(LLMImageOptions=lambda **kwargs: types.SimpleNamespace(**kwargs))
    monkeypatch.setitem(sys.modules, "src.common.data_models.llm_service_data_models", llm_options_mod)

    # If module already imported, reload it to apply patches
    mod_name = "src.chat.image_system.image_manager"
    if mod_name in sys.modules:
        importlib.reload(sys.modules[mod_name])

    yield


def _load_image_manager_module(tmp_path=None):
    repo_root = Path(__file__).parent.parent.parent
    file_path = repo_root / "src" / "chat" / "image_system" / "image_manager.py"
    spec = importlib.util.spec_from_file_location("image_manager_test_loaded", str(file_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    # Redirect IMAGE_DIR to pytest's tmp_path when provided
    try:
        if tmp_path is not None:
            tmpdir = Path(tmp_path)
            tmpdir.mkdir(parents=True, exist_ok=True)
            mod.IMAGE_DIR = tmpdir
    except Exception:
        pass
    return mod


@pytest.mark.asyncio
async def test_get_image_description_generates(monkeypatch, tmp_path):
    image_manager = _load_image_manager_module(tmp_path)
    monkeypatch.setattr(image_manager, "_is_vlm_task_configured", lambda: True)
    monkeypatch.setattr(image_manager, "vlm", DummyLLMServiceClient())

    mgr = image_manager.ImageManager()
    desc = await mgr.get_image_description(image_bytes=b"abc")
    assert desc == "dummy description"


def test_get_image_from_db_none(tmp_path):
    image_manager = _load_image_manager_module(tmp_path)

    mgr = image_manager.ImageManager()
    assert mgr.get_image_from_db("nohash") is None


def test_register_image_to_db(tmp_path):
    image_manager = _load_image_manager_module(tmp_path)

    mgr = image_manager.ImageManager()
    p = tmp_path / "img.png"
    p.write_bytes(b"data")
    img = DummyMaiImage(full_path=p, image_bytes=b"data")
    assert mgr.register_image_to_db(img) is True


def test_update_image_description_not_found(tmp_path):
    image_manager = _load_image_manager_module(tmp_path)

    mgr = image_manager.ImageManager()
    img = DummyMaiImage()
    img.file_hash = "nohash"
    img.description = "desc"
    assert mgr.update_image_description(img) is False


def test_delete_image_not_found(tmp_path):
    image_manager = _load_image_manager_module(tmp_path)

    mgr = image_manager.ImageManager()
    img = DummyMaiImage()
    img.file_hash = "nohash"
    img.full_path = tmp_path = None
    assert mgr.delete_image(img) is False


@pytest.mark.asyncio
async def test_save_image_and_process_and_cleanup(monkeypatch, tmp_path):
    image_manager = _load_image_manager_module(tmp_path)
    monkeypatch.setattr(image_manager, "_is_vlm_task_configured", lambda: True)
    monkeypatch.setattr(image_manager, "vlm", DummyLLMServiceClient())

    mgr = image_manager.ImageManager()
    # call save_image_and_process
    image = await mgr.save_image_and_process(b"binarydata")
    assert getattr(image, "description", None) == "dummy description"

    # cleanup should run without error
    mgr.cleanup_invalid_descriptions_in_db()


@pytest.mark.asyncio
async def test_get_image_description_returns_cached_description_after_session_closed(monkeypatch, tmp_path):
    image_manager = _load_image_manager_module(tmp_path)

    cached_record = DetachedRecord()
    monkeypatch.setattr(image_manager, "get_db_session", lambda: DetachedRecordSession(cached_record))

    mgr = image_manager.ImageManager()
    desc = await mgr.get_image_description(image_hash="cached-hash", wait_for_build=False)

    assert desc == "cached description"
