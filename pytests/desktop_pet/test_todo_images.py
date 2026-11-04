from io import BytesIO
import base64

from PIL import Image
import pytest

from src.desktop_pet.todos import TodoStore


def image_url():
    output = BytesIO()
    Image.new("RGB", (8, 8), "orange").save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def test_images_survive_edit_archive_and_restart(tmp_path):
    path = tmp_path / "todos.json"
    store = TodoStore(path)
    source = image_url()
    item = store.add("图片备忘", images=[source])
    image_id = item["images"][0]
    assert len(image_id) == 64
    assert source not in path.read_text("utf-8")
    assert store.image(image_id) == source
    edited = store.update(item["id"], "改个标题")
    assert edited["images"] == [image_id]
    store.done(item["id"])
    restarted = TodoStore(path)
    assert restarted.list(include_completed=True)[0]["images"] == [image_id]
    assert restarted.image(image_id) == source


@pytest.mark.parametrize("images", [["../../secrets"], ["data:image/png;base64,YmFk"], [image_url()] * 5])
def test_rejects_invalid_images_without_saving_todo(tmp_path, images):
    store = TodoStore(tmp_path / "todos.json")
    with pytest.raises(ValueError):
        store.add("备忘", images=images)
    assert store.list() == []
