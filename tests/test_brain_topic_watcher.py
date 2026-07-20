import importlib.util
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = (HERE.parent / "scripts" / "brain_topic_watcher.py") if (HERE.parent / "scripts" / "brain_topic_watcher.py").exists() else (HERE / "brain_topic_watcher.py")
CATALOG = (HERE.parent / "scripts" / "brain_topic_catalog.py") if (HERE.parent / "scripts" / "brain_topic_catalog.py").exists() else (HERE / "brain_topic_catalog.py")
spec = importlib.util.spec_from_file_location("brainwatch", SCRIPT)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)


def test_parse_media_and_description():
    text, media, sender = mod.parse_content("[J|6218150306]\nremember: likes concise notes\n\n[image 'x.jpg' saved at: /tmp/x.jpg]")
    assert sender == "6218150306"
    assert text == "remember: likes concise notes"
    assert media == ["/tmp/x.jpg"]
    assert mod.memory_type(text) == "preference"


def test_unconfigured_is_silent_ready(tmp_path):
    cfg = tmp_path / "config.json"; cfg.write_text("{}")
    result = mod.run_once(config_path=cfg, state_path=tmp_path/"state.json", db_path=tmp_path/"none.db", catalog_path=tmp_path/"none.py")
    assert result["status"] == "unconfigured"


def test_topic_row_is_ingested(tmp_path):
    db = tmp_path / "state.db"; con = sqlite3.connect(db)
    con.executescript("CREATE TABLE sessions(id TEXT PRIMARY KEY,chat_id TEXT,thread_id TEXT); CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,platform_message_id TEXT);")
    con.execute("INSERT INTO sessions VALUES('s','-100','57')")
    con.execute("INSERT INTO messages VALUES(1,'s','user','[J|6218150306]\\nalpha note','99')"); con.commit()
    cfg = tmp_path / "config.json"; cfg.write_text(json.dumps({"chat_id":"-100","thread_id":"57","allowed_sender_ids":["6218150306"],"catalog_root":str(tmp_path/"brain")}))
    result = mod.run_once(config_path=cfg,state_path=tmp_path/"cursor.json",db_path=db,catalog_path=CATALOG)
    assert result["processed"] == 1
    assert json.loads((tmp_path/"cursor.json").read_text())["last_db_message_id"] == 1
