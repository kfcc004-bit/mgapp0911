
"""Streamlit dashboard for generic CRUD against Supabase Data API.

Run:
    pip install streamlit pandas requests
    streamlit run supabase_crud_dashboard.py
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st


st.set_page_config(
    page_title="Supabase DB 통합관리",
    page_icon="🗄️",
    layout="wide",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 2rem; padding-bottom: 3rem;}
      div[data-testid="stMetric"] {
        background: white; border: 1px solid #e2e8f0;
        padding: 1rem; border-radius: .8rem;
      }
      .warning-box {
        padding: .9rem 1rem; border-radius: .7rem;
        background: #fff7ed; border: 1px solid #fed7aa; color: #9a3412;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

REQUEST_TIMEOUT = 20
ROW_LIMIT = 1_000

# 현재 프로젝트에서 확인한 테이블/컬럼 스키마입니다.
# 실제 행 데이터는 포함하지 않으며 모든 데이터는 실행 시 Supabase에서 조회합니다.
TABLE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "branches": {
        "type": "object",
        "required": ["branch_id", "branch_name", "region", "manager_name"],
        "properties": {
            "branch_id": {"type": "integer", "description": "Primary Key <pk/>"},
            "branch_name": {"type": "string"},
            "region": {"type": "string"},
            "manager_name": {"type": "string"},
        },
    },
    "members": {
        "type": "object",
        "required": ["member_id", "name", "birth_date", "gender", "join_date", "branch_id", "phone"],
        "properties": {
            "member_id": {"type": "integer", "description": "Primary Key <pk/>"},
            "name": {"type": "string"},
            "birth_date": {"type": "string", "format": "date"},
            "gender": {"type": "string", "enum": ["M", "F"]},
            "join_date": {"type": "string", "format": "date"},
            "branch_id": {"type": "integer"},
            "phone": {"type": "string"},
        },
    },
    "deposit_accounts": {
        "type": "object",
        "required": ["account_id", "member_id", "branch_id", "account_type", "open_date", "balance", "interest_rate"],
        "properties": {
            "account_id": {"type": "integer", "description": "Primary Key <pk/>"},
            "member_id": {"type": "integer"},
            "branch_id": {"type": "integer"},
            "account_type": {"type": "string"},
            "open_date": {"type": "string", "format": "date"},
            "balance": {"type": "number"},
            "interest_rate": {"type": "number"},
        },
    },
    "loans": {
        "type": "object",
        "required": ["loan_id", "member_id", "branch_id", "loan_type", "loan_amount", "interest_rate", "start_date", "due_date", "status"],
        "properties": {
            "loan_id": {"type": "integer", "description": "Primary Key <pk/>"},
            "member_id": {"type": "integer"},
            "branch_id": {"type": "integer"},
            "loan_type": {"type": "string"},
            "loan_amount": {"type": "number"},
            "interest_rate": {"type": "number"},
            "start_date": {"type": "string", "format": "date"},
            "due_date": {"type": "string", "format": "date"},
            "status": {"type": "string"},
        },
    },
    "transactions": {
        "type": "object",
        "required": ["transaction_id", "account_id", "transaction_date", "transaction_type", "amount", "balance_after"],
        "properties": {
            "transaction_id": {"type": "integer", "description": "Primary Key <pk/>"},
            "account_id": {"type": "integer"},
            "transaction_date": {"type": "string", "format": "date-time"},
            "transaction_type": {"type": "string"},
            "amount": {"type": "number"},
            "balance_after": {"type": "number"},
        },
    },
    "deposit_rate_comparisons": {
        "type": "object",
        "required": ["reference_date", "institution_name", "sector", "product_name", "base_rate", "maximum_rate"],
        "properties": {
            "id": {"type": "integer", "description": "Primary Key <pk/>; identity", "readOnly": True},
            "reference_date": {"type": "string", "format": "date"},
            "institution_name": {"type": "string"},
            "sector": {"type": "string", "enum": ["시중은행", "상호금융", "저축은행"]},
            "product_name": {"type": "string"},
            "base_rate": {"type": "number"},
            "maximum_rate": {"type": "number"},
            "preferential_conditions": {"type": "string"},
            "created_at": {"type": "string", "format": "date-time", "readOnly": True},
            "updated_at": {"type": "string", "format": "date-time", "readOnly": True},
        },
    },
}


def clean_url(value: str) -> str:
    return value.strip().rstrip("/")


def is_privileged_key(key: str) -> bool:
    """Block secret/service-role keys from a browser-like dashboard."""
    if key.strip().startswith("sb_secret_"):
        return True
    parts = key.strip().split(".")
    if len(parts) != 3:
        return False
    try:
        import base64

        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(payload).decode("utf-8")
        return json.loads(decoded).get("role") == "service_role"
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def headers(prefer: str | None = None) -> dict[str, str]:
    result = {
        "apikey": st.session_state.api_key,
        "Content-Type": "application/json",
    }
    if prefer:
        result["Prefer"] = prefer
    return result


def api_request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    prefer: str | None = None,
) -> Any:
    response = requests.request(
        method,
        f"{st.session_state.supabase_url}{path}",
        headers=headers(prefer),
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        try:
            detail = response.json()
            message = detail.get("message") or detail.get("hint") or str(detail)
        except (ValueError, AttributeError):
            message = response.text or f"HTTP {response.status_code}"
        raise RuntimeError(f"{response.status_code}: {message}")
    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def primary_key(definition: dict[str, Any]) -> str | None:
    for name, prop in definition.get("properties", {}).items():
        description = str(prop.get("description", "")).lower()
        if "<pk/>" in description or "primary key" in description:
            return name
    return None


def is_generated(prop: dict[str, Any]) -> bool:
    description = str(prop.get("description", "")).lower()
    return (
        "identity" in description
        or "generated" in description
        or prop.get("readOnly") is True
    )


def fetch_rows(table: str, pk: str | None) -> list[dict[str, Any]]:
    order = f"&order={quote(pk)}.asc" if pk else ""
    return api_request(
        "GET",
        f"/rest/v1/{quote(table)}?select=*&limit={ROW_LIMIT}{order}",
    )


def widget_value(
    field: str,
    prop: dict[str, Any],
    value: Any,
    *,
    key_prefix: str,
) -> Any:
    label = field
    widget_key = f"{key_prefix}_{field}"
    prop_type = prop.get("type", "string")
    prop_format = prop.get("format", "")
    description = prop.get("description")
    help_text = description if description and len(str(description)) < 180 else None

    if prop_type == "boolean":
        return st.checkbox(label, value=bool(value), key=widget_key, help=help_text)
    if prop_type in {"integer", "number"}:
        numeric_value = None if value in (None, "") else value
        step = 1 if prop_type == "integer" else 0.01
        return st.number_input(
            label,
            value=numeric_value,
            step=step,
            key=widget_key,
            help=help_text,
        )
    if prop_format == "date":
        parsed = date.today()
        if value:
            try:
                parsed = date.fromisoformat(str(value)[:10])
            except ValueError:
                pass
        return st.date_input(label, value=parsed, key=widget_key, help=help_text).isoformat()
    if prop_format in {"date-time", "timestamp", "timestamptz"}:
        parsed_dt = datetime.now().replace(microsecond=0)
        if value:
            try:
                parsed_dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                pass
        return st.text_input(
            label,
            value=parsed_dt.isoformat(sep=" "),
            key=widget_key,
            help=help_text,
        )
    if prop.get("enum"):
        options = list(prop["enum"])
        index = options.index(value) if value in options else 0
        return st.selectbox(label, options, index=index, key=widget_key, help=help_text)
    text = "" if value is None else str(value)
    if len(text) > 100 or "description" in field or "conditions" in field:
        return st.text_area(label, value=text, key=widget_key, help=help_text)
    return st.text_input(label, value=text, key=widget_key, help=help_text)


def row_label(row: dict[str, Any], pk: str, index: int) -> str:
    extras = [str(v) for k, v in row.items() if k != pk and v not in (None, "")][:2]
    suffix = " · ".join(extras)
    return f"{pk}={row.get(pk)}" + (f" | {suffix}" if suffix else f" | #{index + 1}")


def connect() -> None:
    url = clean_url(st.session_state.url_input)
    key = st.session_state.key_input.strip()
    if not url.startswith("https://") or not key:
        st.sidebar.error("올바른 HTTPS URL과 공개 키를 입력하세요.")
        return
    if is_privileged_key(key):
        st.sidebar.error("secret/service_role 키는 사용할 수 없습니다.")
        return
    st.session_state.supabase_url = url
    st.session_state.api_key = key
    try:
        first_table = next(iter(TABLE_DEFINITIONS))
        api_request("GET", f"/rest/v1/{first_table}?select=*&limit=1")
        st.session_state.tables = TABLE_DEFINITIONS
        st.session_state.connected = True
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        st.session_state.connected = False
        st.sidebar.error(str(exc))


for key, default in {
    "connected": False,
    "supabase_url": "",
    "api_key": "",
    "tables": {},
}.items():
    st.session_state.setdefault(key, default)

st.title("🗄️ Supabase DB 통합관리")
st.caption("현재 Supabase 프로젝트의 모든 6개 테이블을 조회·등록·수정·삭제합니다.")

with st.sidebar:
    st.header("DB 연결")
    st.text_input(
        "Supabase URL",
        placeholder="https://project-ref.supabase.co",
        key="url_input",
    )
    st.text_input(
        "Publishable / anon key",
        type="password",
        placeholder="브라우저용 공개 키",
        key="key_input",
    )
    st.button("연결 및 테이블 조회", type="primary", use_container_width=True, on_click=connect)
    st.markdown(
        '<div class="warning-box">로그인 없이 CRUD가 가능한 프로젝트에서는 공개 키를 가진 누구나 데이터를 변경·삭제할 수 있습니다. secret/service_role 키는 입력하지 마세요.</div>',
        unsafe_allow_html=True,
    )

if not st.session_state.connected:
    st.info("왼쪽에서 Supabase URL과 publishable/anon 키를 입력해 연결하세요.")
    st.stop()

table_names = sorted(st.session_state.tables)
selected_table = st.sidebar.selectbox("테이블", table_names)
definition = st.session_state.tables[selected_table]
properties = definition.get("properties", {})
required = set(definition.get("required", []))
pk = primary_key(definition)

try:
    rows = fetch_rows(selected_table, pk)
except (requests.RequestException, RuntimeError) as exc:
    st.error(f"데이터 조회 실패: {exc}")
    st.stop()

col1, col2, col3 = st.columns(3)
col1.metric("선택 테이블", selected_table)
col2.metric("조회 건수", f"{len(rows):,}건")
col3.metric("기본키", pk or "자동 감지 실패")

if len(rows) >= ROW_LIMIT:
    st.warning(f"화면 성능을 위해 최대 {ROW_LIMIT:,}건만 표시합니다.")

tab_read, tab_create, tab_update, tab_delete = st.tabs(
    ["조회", "신규 등록", "수정", "삭제"]
)

with tab_read:
    search = st.text_input("전체 컬럼 검색", placeholder="검색어 입력")
    frame = pd.DataFrame(rows)
    if search and not frame.empty:
        mask = frame.astype(str).apply(
            lambda column: column.str.contains(search, case=False, na=False)
        ).any(axis=1)
        frame = frame[mask]
    st.dataframe(frame, use_container_width=True, hide_index=True)
    st.download_button(
        "CSV 다운로드",
        data=frame.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{selected_table}.csv",
        mime="text/csv",
        disabled=frame.empty,
    )

with tab_create:
    with st.form(f"create_{selected_table}", clear_on_submit=True):
        new_row: dict[str, Any] = {}
        for field, prop in properties.items():
            if is_generated(prop):
                continue
            new_row[field] = widget_value(
                field, prop, None, key_prefix=f"create_{selected_table}"
            )
        create_submitted = st.form_submit_button("DB에 등록", type="primary")
    if create_submitted:
        payload = {
            key: value
            for key, value in new_row.items()
            if value not in ("", None) or key in required
        }
        try:
            api_request(
                "POST",
                f"/rest/v1/{quote(selected_table)}",
                payload=payload,
                prefer="return=representation",
            )
            st.success("등록되었습니다.")
            st.rerun()
        except (requests.RequestException, RuntimeError) as exc:
            st.error(f"등록 실패: {exc}")

with tab_update:
    if not rows:
        st.info("수정할 데이터가 없습니다.")
    elif not pk:
        st.warning("기본키를 자동 감지하지 못해 수정 기능을 사용할 수 없습니다.")
    else:
        labels = [row_label(row, pk, i) for i, row in enumerate(rows)]
        selected_index = st.selectbox(
            "수정할 행",
            range(len(rows)),
            format_func=lambda i: labels[i],
            key=f"update_selector_{selected_table}",
        )
        target = rows[selected_index]
        with st.form(f"update_{selected_table}"):
            st.text_input(pk, value=str(target.get(pk, "")), disabled=True)
            updated: dict[str, Any] = {}
            for field, prop in properties.items():
                if field == pk or is_generated(prop):
                    continue
                updated[field] = widget_value(
                    field,
                    prop,
                    target.get(field),
                    key_prefix=f"update_{selected_table}_{target.get(pk)}",
                )
            update_submitted = st.form_submit_button("변경사항 저장", type="primary")
        if update_submitted:
            try:
                api_request(
                    "PATCH",
                    f"/rest/v1/{quote(selected_table)}?{quote(pk)}=eq.{quote(str(target[pk]))}",
                    payload=updated,
                    prefer="return=representation",
                )
                st.success("수정되었습니다.")
                st.rerun()
            except (requests.RequestException, RuntimeError) as exc:
                st.error(f"수정 실패: {exc}")

with tab_delete:
    if not rows:
        st.info("삭제할 데이터가 없습니다.")
    elif not pk:
        st.warning("기본키를 자동 감지하지 못해 삭제 기능을 사용할 수 없습니다.")
    else:
        labels = [row_label(row, pk, i) for i, row in enumerate(rows)]
        delete_index = st.selectbox(
            "삭제할 행",
            range(len(rows)),
            format_func=lambda i: labels[i],
            key=f"delete_selector_{selected_table}",
        )
        delete_target = rows[delete_index]
        st.json(delete_target)
        confirmation = st.text_input(
            '삭제하려면 "DELETE"를 입력하세요.',
            key=f"delete_confirm_{selected_table}_{delete_target.get(pk)}",
        )
        if st.button(
            "영구 삭제",
            type="primary",
            disabled=confirmation != "DELETE",
            key=f"delete_button_{selected_table}_{delete_target.get(pk)}",
        ):
            try:
                api_request(
                    "DELETE",
                    f"/rest/v1/{quote(selected_table)}?{quote(pk)}=eq.{quote(str(delete_target[pk]))}",
                    prefer="return=representation",
                )
                st.success("삭제되었습니다.")
                st.rerun()
            except (requests.RequestException, RuntimeError) as exc:
                st.error(f"삭제 실패: {exc}")

st.caption(
    "외래키로 연결된 행이 있으면 삭제가 거부될 수 있습니다. 모든 변경은 Supabase DB에 즉시 반영됩니다."
)
