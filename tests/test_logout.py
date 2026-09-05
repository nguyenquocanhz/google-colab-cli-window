# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""`colab logout` must not strand a running runtime.

Signing out is trivial on its own -- remove one file. The part worth testing
is the refusal: a Colab runtime keeps running (and keeps holding the quota)
after the token is gone, and without the token this CLI can no longer reach
it to call `stop`. That is exactly the orphaned-runtime state this fork added
`colab stop --endpoint` to dig out of, so the sign-out path must not be a new
way to fall into it.

The second thing under test is quieter but was the real bug: the command must
not touch the network. It reads the LOCAL session store, never
`sync_sessions()`, because building a client mints credentials and opens a
browser consent flow -- and an expired token is the usual reason someone
types `logout` in the first place.
"""

import pytest
from typer.testing import CliRunner

from colab_cli.cli import app

runner = CliRunner()


@pytest.fixture
def mock_store(mock_common_state):
    """The local `sessions.json` reader, mocked by the autouse fixture.

    Same fixture shape as `test_cli.py` uses. Default it to empty: the
    conftest hands back a bare `MagicMock`, which is truthy and does not
    iterate, so a test that forgets to set this would trip the refusal path
    for the wrong reason.
    """
    mock_common_state.store.list.return_value = {}
    return mock_common_state.store


@pytest.fixture
def nha(tmp_path, monkeypatch):
    """A config home with a token in it."""
    monkeypatch.setenv("COLAB_CLI_HOME", str(tmp_path))
    p = tmp_path / "token.json"
    p.write_text('{"refresh_token": "khong-phai-that"}', encoding="utf-8")
    return p


def _co_phien(mock_store, n=1):
    mock_store.list.return_value = {f"gpu-t4-s-{i}": object() for i in range(n)}


def test_xoa_token_khi_khong_con_phien(nha, mock_store):
    r = runner.invoke(app, ["logout"])
    assert r.exit_code == 0, r.output
    assert not nha.exists()
    assert "Signed out" in r.output


def test_tu_choi_khi_con_phien_chay(nha, mock_store):
    _co_phien(mock_store, 2)
    r = runner.invoke(app, ["logout"])
    assert r.exit_code == 1
    # Điều quan trọng nhất: token PHẢI CÒN. Xoá nó là mất đường gọi `stop`.
    assert nha.exists(), "token bị xoá dù còn runtime đang chạy"
    assert "2 session(s) recorded locally" in r.output
    assert "gpu-t4-s-0" in r.output
    assert "gpu-t4-s-1" in r.output


def test_khong_goi_mang_de_dang_xuat(nha, mock_common_state, mock_store):
    """Đăng xuất KHÔNG được đụng tới mạng.

    `sync_sessions()` dựng client -> lấy credentials -> mở trình duyệt xin
    quyền. Bản đầu tiên của lệnh này gọi đúng như vậy: chạy `pytest` mà
    requests_oauthlib in ra "Generated new state ...", rồi lỗi bị nuốt và
    token vẫn bị xoá. Đăng xuất mà phải đăng nhập trước là vô lý, và lúc
    token đã hỏng -- đúng lúc người ta cần `logout` -- thì nó còn chết hẳn.
    """
    _co_phien(mock_store, 1)
    r = runner.invoke(app, ["logout"])
    assert r.exit_code == 1
    mock_common_state.sync_sessions.assert_not_called()
    assert not mock_common_state.client.called


def test_force_xoa_ke_ca_khi_con_phien(nha, mock_store):
    _co_phien(mock_store, 1)
    r = runner.invoke(app, ["logout", "--force"])
    assert r.exit_code == 0, r.output
    assert not nha.exists()


def test_force_khong_can_doc_so_phien(nha, mock_store):
    """`--force` tồn tại cho lúc chính việc liệt kê cũng hỏng -- đừng liệt kê.

    Nếu `--force` vẫn gọi `store.list()` thì một `sessions.json` hỏng sẽ chặn
    luôn cả đường thoát cuối cùng.
    """
    mock_store.list.side_effect = OSError("sessions.json hỏng")
    r = runner.invoke(app, ["logout", "--force"])
    assert r.exit_code == 0, r.output
    assert not nha.exists()


def test_bao_khi_khong_doc_duoc_so_phien(nha, mock_store):
    """Không đọc được sổ phiên thì vẫn đăng xuất, nhưng phải NÓI ra.

    Chặn lại thì người dùng hết đường đăng xuất; im lặng thì họ tưởng đã
    kiểm tra xong mà thật ra chưa.
    """
    mock_store.list.side_effect = OSError("sessions.json hỏng")
    r = runner.invoke(app, ["logout"])
    assert r.exit_code == 0, r.output
    assert not nha.exists()
    assert "could not read the session list" in r.output


def test_khong_co_token_thi_khong_phai_loi(tmp_path, monkeypatch, mock_store):
    """Đăng xuất hai lần liên tiếp không được coi là hỏng."""
    monkeypatch.setenv("COLAB_CLI_HOME", str(tmp_path))
    r = runner.invoke(app, ["logout"])
    assert r.exit_code == 0, r.output
    assert "Already signed out" in r.output


def test_chi_xoa_token_giu_lai_phan_con_lai(nha, mock_store, tmp_path):
    """`sessions.json` và `settings.json` không chứa bí mật -- giữ lại.

    Xoá `sessions.json` cùng lúc thì tên các runtime biến mất, mà tên là thứ
    duy nhất `colab stop` nhận -- lại rơi vào chính cái bẫy runtime mồ côi.
    """
    (tmp_path / "sessions.json").write_text("{}", encoding="utf-8")
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    r = runner.invoke(app, ["logout"])
    assert r.exit_code == 0, r.output
    assert not nha.exists()
    assert (tmp_path / "sessions.json").exists()
    assert (tmp_path / "settings.json").exists()


def test_ton_trong_COLAB_CLI_HOME(tmp_path, monkeypatch, mock_store):
    """Đăng xuất phải xoá token của HỒ SƠ ĐANG DÙNG, không phải mặc định.

    Không kiểm điều này thì `logout` có thể lặng lẽ xoá token ở
    `~/.config/colab-cli` trong khi người dùng đang ở một hồ sơ khác -- đăng
    xuất nhầm tài khoản, và tài khoản định đăng xuất thì vẫn còn nguyên.
    """
    rieng = tmp_path / "ho-so-cong-viec"
    rieng.mkdir()
    (rieng / "token.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("COLAB_CLI_HOME", str(rieng))
    r = runner.invoke(app, ["logout"])
    assert r.exit_code == 0, r.output
    assert not (rieng / "token.json").exists()
    assert str(rieng) in r.output


def test_khong_chay_kiem_tra_cap_nhat(nha, mock_store, mocker):
    """`logout` phải nằm trong danh sách chặn tự-cập-nhật.

    `run_background_check()` gọi ra PyPI. Đây là lệnh KHÔNG được đụng mạng --
    cùng lý do với `sync_sessions` -- và nó còn in ba dòng quảng cáo bản mới
    lấp mất một dòng kết quả duy nhất, đúng cái cớ khiến `whoami` được chặn
    từ trước.
    """
    goi = mocker.patch("colab_cli.auto_update.run_background_check")
    r = runner.invoke(app, ["logout"])
    assert r.exit_code == 0, r.output
    goi.assert_not_called()


def test_thong_bao_thuan_ascii(nha, mock_store):
    """Chữ in ra phải thuần ASCII, không thì console cp1252 hiện ra dấu hỏi.

    Luồng ra được đặt `errors="replace"`, nên một dấu gạch dài không làm chết
    lệnh -- nó lặng lẽ biến thành `?`. Cả kho không có `typer.echo` nào chứa
    ký tự ngoài ASCII; chỗ này từng là ngoại lệ duy nhất.
    """
    _co_phien(mock_store, 1)
    r = runner.invoke(app, ["logout"])
    ngoai = [c for c in r.output if ord(c) > 127]
    assert not ngoai, f"ky tu ngoai ASCII trong dau ra: {ngoai!r}"
