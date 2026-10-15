import asyncio
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main


def minimal_glb():
    payload = json.dumps({"asset": {"version": "2.0"}}, separators=(",", ":")).encode()
    payload += b" " * ((4 - len(payload) % 4) % 4)
    total = 12 + 8 + len(payload)
    return b"glTF" + struct.pack("<II", 2, total) + struct.pack("<II", len(payload), 0x4E4F534A) + payload


class UploadRequest:
    def __init__(self, body, declared=None):
        self.body = body
        self.headers = {} if declared is False else {
            "content-length": str(len(body) if declared is None else declared)
        }

    async def stream(self):
        yield self.body


class SceneSecurityTests(unittest.TestCase):
    def test_upload_rejects_path_traversal_and_non_glb_names(self):
        request = UploadRequest(b"ignored")
        for filename in ("../escape.glb", "/tmp/escape.glb", "escape.obj", "bad\nname.glb"):
            with self.subTest(filename=filename):
                with self.assertRaises(main.HTTPException) as raised:
                    asyncio.run(main.api_scene_upload(request, filename))
                self.assertEqual(raised.exception.status_code, 400)

    def test_upload_rejects_corrupt_glb_without_leaving_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with patch.object(main, "SCENE_DIR", directory):
                with self.assertRaises(main.HTTPException) as raised:
                    asyncio.run(main.api_scene_upload(UploadRequest(b"not-a-glb"), "broken.glb"))
                self.assertEqual(raised.exception.status_code, 400)
                self.assertEqual(list(directory.iterdir()), [])

    def test_upload_is_atomic_and_persists_sha256(self):
        body = minimal_glb()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with patch.object(main, "SCENE_DIR", directory):
                result = asyncio.run(main.api_scene_upload(UploadRequest(body), "fixture.glb"))
                self.assertTrue(result["ok"])
                model = directory / result["filename"]
                draft = directory / f"{result['id']}.scene-v2.json"
                self.assertTrue(model.is_file())
                self.assertTrue(draft.is_file())
                self.assertEqual(result["sha256"], hashlib.sha256(body).hexdigest())
                self.assertEqual(list(directory.glob("*.upload")), [])


    def test_chunked_upload_without_content_length_is_atomic(self):
        body = minimal_glb()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with patch.object(main, "SCENE_DIR", directory):
                result = asyncio.run(main.api_scene_upload(UploadRequest(body, declared=False), "chunked.glb"))
                self.assertTrue(result["ok"])
                self.assertEqual((directory / result["filename"]).read_bytes(), body)
                self.assertEqual(list(directory.glob("*.upload")), [])

    def test_chunked_upload_is_rejected_when_reserved_space_budget_is_exceeded(self):
        body = minimal_glb()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            with patch.object(main, "SCENE_DIR", directory), \
                 patch.object(main.shutil, "disk_usage", return_value=SimpleNamespace(free=64 * 1024 * 1024 + 1)):
                with self.assertRaises(main.HTTPException) as raised:
                    asyncio.run(main.api_scene_upload(UploadRequest(body, declared=False), "full.glb"))
                self.assertEqual(raised.exception.status_code, 507)
                self.assertEqual(list(directory.iterdir()), [])

    def test_model_hash_mismatch_is_rejected(self):
        body = minimal_glb()
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "model.glb"
            model.write_bytes(body)
            scene = {"model": {"sha256": "0" * 64}}
            with self.assertRaises(main.HTTPException) as raised:
                main._verify_scene_model_hash(scene, model)
            self.assertEqual(raised.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
