import json

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st


# =========================================================
# 기본 설정
# =========================================================
st.set_page_config(
    page_title="전국 고령화 단계구분도",
    page_icon="🗺️",
    layout="wide",
)

POPULATION_URL = (
    "https://raw.githubusercontent.com/greatsong/modudata/"
    "main/data/population_yearly.csv.gz"
)

GEOJSON_URL = (
    "https://raw.githubusercontent.com/greatsong/modudata/"
    "main/data/boundaries/sigungu_kr.geojson"
)


# =========================================================
# 공통 함수
# =========================================================
def clean_code(value, length=None):
    """
    지역 코드를 문자열로 정리한다.

    예:
    1111053000.0 -> 1111053000
    11110        -> 11110
    """
    if pd.isna(value):
        return None

    code = str(value).strip()

    if code.endswith(".0"):
        code = code[:-2]

    # 숫자가 아닌 문자가 섞인 경우를 대비
    code = "".join(character for character in code if character.isdigit())

    if not code:
        return None

    if length is not None:
        code = code.zfill(length)[:length]

    return code


# =========================================================
# 인구 데이터 불러오기 및 계산
# =========================================================
@st.cache_data(show_spinner=False)
def load_population_data():
    """
    압축 CSV를 불러오고 2026년 자료만 이용해
    시군구별 65세 이상 인구 비율을 계산한다.
    """
    population = pd.read_csv(
        POPULATION_URL,
        compression="gzip",
        dtype={
            "연도": "string",
            "시도": "string",
            "시군구": "string",
            "동": "string",
            "코드": "string",
        },
        low_memory=False,
    )

    required_columns = {"연도", "시도", "시군구", "동", "코드"}
    missing_columns = required_columns - set(population.columns)

    if missing_columns:
        raise ValueError(
            "인구 데이터에 필요한 열이 없습니다: "
            + ", ".join(sorted(missing_columns))
        )

    # 연도 값에 '년' 등이 포함되어 있어도 2026년을 찾을 수 있도록 처리
    year_numeric = pd.to_numeric(
        population["연도"].astype(str).str.extract(r"(\d{4})")[0],
        errors="coerce",
    )

    population = population.loc[year_numeric == 2026].copy()

    if population.empty:
        raise ValueError("인구 데이터에서 2026년 자료를 찾지 못했습니다.")

    # 동 코드를 문자열로 정리한 뒤 앞 5자리를 시군구 코드로 사용
    population["시군구코드"] = population["코드"].map(clean_code).str[:5]

    population = population.dropna(subset=["시군구코드"])
    population = population.loc[
        population["시군구코드"].str.len() == 5
    ].copy()

    # 전체 인구: '계_'로 시작하는 모든 연령별 열
    total_columns = [
        column
        for column in population.columns
        if str(column).startswith("계_")
    ]

    # 65세 이상 인구: 계_65세 ~ 계_99세, 계_100세 이상
    elderly_columns = []

    for column in total_columns:
        column_text = str(column)

        if column_text == "계_100세 이상":
            elderly_columns.append(column)
            continue

        age_text = (
            column_text
            .replace("계_", "")
            .replace("세", "")
            .strip()
        )

        if age_text.isdigit() and int(age_text) >= 65:
            elderly_columns.append(column)

    if not total_columns:
        raise ValueError("'계_'로 시작하는 인구 열을 찾지 못했습니다.")

    if not elderly_columns:
        raise ValueError("65세 이상 인구 열을 찾지 못했습니다.")

    # 쉼표가 포함된 문자열도 숫자로 변환
    numeric_columns = list(set(total_columns + elderly_columns))

    for column in numeric_columns:
        population[column] = pd.to_numeric(
            population[column]
            .astype(str)
            .str.replace(",", "", regex=False)
            .str.strip(),
            errors="coerce",
        ).fillna(0)

    # 동별 전체 인구와 65세 이상 인구
    population["전체인구"] = population[total_columns].sum(axis=1)
    population["65세이상인구"] = population[elderly_columns].sum(axis=1)

    # 시군구 코드 앞 5자리로 합산
    sigungu_population = (
        population.groupby("시군구코드", as_index=False)
        .agg(
            전체인구=("전체인구", "sum"),
            고령인구=("65세이상인구", "sum"),
        )
    )

    sigungu_population["고령화율"] = (
        sigungu_population["고령인구"]
        .div(sigungu_population["전체인구"])
        .mul(100)
    )

    sigungu_population["고령화율"] = (
        sigungu_population["고령화율"]
        .replace([float("inf"), float("-inf")], pd.NA)
    )

    return sigungu_population


# =========================================================
# GeoJSON 불러오기
# =========================================================
@st.cache_data(show_spinner=False)
def load_geojson():
    response = requests.get(GEOJSON_URL, timeout=60)
    response.raise_for_status()

    geojson = response.json()

    if "features" not in geojson:
        raise ValueError("GeoJSON에 features 항목이 없습니다.")

    boundary_rows = []

    for feature in geojson["features"]:
        properties = feature.setdefault("properties", {})

        code = clean_code(properties.get("코드"), length=5)
        properties["코드"] = code

        boundary_rows.append(
            {
                "코드": code,
                "시도": properties.get("시도", ""),
                "시군구": properties.get("시군구", ""),
            }
        )

    boundaries = pd.DataFrame(boundary_rows)
    boundaries = boundaries.dropna(subset=["코드"])
    boundaries = boundaries.drop_duplicates(subset=["코드"])

    return geojson, boundaries


# =========================================================
# 데이터 준비
# =========================================================
st.title("🗺️ 전국 고령화 단계구분도")
st.caption(
    "2026년 6월 읍·면·동 인구를 시군구 단위로 합산한 "
    "65세 이상 인구 비율입니다."
)

try:
    with st.spinner("인구 데이터와 행정구역 경계를 불러오는 중입니다."):
        aging_data = load_population_data()
        geojson_data, boundary_data = load_geojson()

except requests.RequestException as error:
    st.error(f"온라인 데이터를 내려받지 못했습니다: {error}")
    st.stop()

except Exception as error:
    st.error(f"데이터 처리 중 오류가 발생했습니다: {error}")
    st.stop()


# GeoJSON 경계 자료를 기준으로 결합
map_data = boundary_data.merge(
    aging_data,
    left_on="코드",
    right_on="시군구코드",
    how="left",
)

map_data["지역명"] = (
    map_data["시도"].fillna("").astype(str)
    + " "
    + map_data["시군구"].fillna("").astype(str)
).str.strip()


# =========================================================
# 상단 요약 정보
# =========================================================
matched_data = map_data.dropna(subset=["고령화율"]).copy()
unmatched_count = int(map_data["고령화율"].isna().sum())

metric_columns = st.columns(4)

with metric_columns[0]:
    st.metric(
        "전국 65세 이상 비율",
        (
            f"{aging_data['고령인구'].sum() / aging_data['전체인구'].sum() * 100:.1f}%"
            if aging_data["전체인구"].sum() > 0
            else "-"
        ),
    )

with metric_columns[1]:
    st.metric(
        "지도 표시 시군구",
        f"{len(matched_data):,}개",
    )

with metric_columns[2]:
    if not matched_data.empty:
        highest_row = matched_data.loc[
            matched_data["고령화율"].idxmax()
        ]

        st.metric(
            "고령화율 최고 지역",
            highest_row["시군구"],
            f"{highest_row['고령화율']:.1f}%",
        )
    else:
        st.metric("고령화율 최고 지역", "-")

with metric_columns[3]:
    if not matched_data.empty:
        lowest_row = matched_data.loc[
            matched_data["고령화율"].idxmin()
        ]

        st.metric(
            "고령화율 최저 지역",
            lowest_row["시군구"],
            f"{lowest_row['고령화율']:.1f}%",
        )
    else:
        st.metric("고령화율 최저 지역", "-")


# =========================================================
# 단계구분도
# =========================================================
if matched_data.empty:
    st.error(
        "인구 데이터의 시군구 코드와 GeoJSON의 코드가 일치하지 않습니다."
    )
    st.stop()


figure = go.Figure(
    go.Choropleth(
        geojson=geojson_data,
        featureidkey="properties.코드",
        locations=matched_data["코드"],
        z=matched_data["고령화율"],
        customdata=matched_data[
            ["시군구", "시도", "코드", "전체인구", "고령인구"]
        ],
        colorscale="Blues",
        reversescale=False,
        marker_line_color="rgba(60, 60, 60, 0.75)",
        marker_line_width=0.45,
        colorbar={
            "title": {
                "text": "65세 이상<br>인구 비율(%)",
            },
            "ticksuffix": "%",
            "thickness": 18,
            "len": 0.75,
        },
        hovertemplate=(
            "<b>%{customdata[0]}</b>"
            "<br>시도: %{customdata[1]}"
            "<br>고령화율: %{z:.1f}%"
            "<br>전체 인구: %{customdata[3]:,.0f}명"
            "<br>65세 이상: %{customdata[4]:,.0f}명"
            "<extra></extra>"
        ),
    )
)

figure.update_geos(
    fitbounds="locations",
    visible=False,
    projection_type="mercator",
    bgcolor="rgba(0,0,0,0)",
)

figure.update_layout(
    title={
        "text": "2026년 전국 시군구별 고령화율",
        "x": 0.5,
        "xanchor": "center",
    },
    height=850,
    margin={
        "l": 0,
        "r": 0,
        "t": 70,
        "b": 0,
    },
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
)

st.plotly_chart(
    figure,
    use_container_width=True,
    config={
        "displayModeBar": True,
        "displaylogo": False,
        "scrollZoom": True,
        "responsive": True,
    },
)


# =========================================================
# 코드 일치 여부 안내
# =========================================================
if unmatched_count > 0:
    with st.expander(
        f"인구 자료가 연결되지 않은 경계 지역: {unmatched_count}개"
    ):
        unmatched_regions = map_data.loc[
            map_data["고령화율"].isna(),
            ["코드", "시도", "시군구"],
        ]

        st.dataframe(
            unmatched_regions,
            use_container_width=True,
            hide_index=True,
        )


with st.expander("시군구별 계산 결과 보기"):
    display_data = matched_data[
        [
            "코드",
            "시도",
            "시군구",
            "전체인구",
            "고령인구",
            "고령화율",
        ]
    ].copy()

    display_data["전체인구"] = display_data["전체인구"].round().astype("Int64")
    display_data["고령인구"] = display_data["고령인구"].round().astype("Int64")
    display_data["고령화율"] = display_data["고령화율"].round(2)

    display_data = display_data.sort_values(
        "고령화율",
        ascending=False,
    )

    st.dataframe(
        display_data,
        use_container_width=True,
        hide_index=True,
        column_config={
            "코드": st.column_config.TextColumn("시군구 코드"),
            "전체인구": st.column_config.NumberColumn(
                "전체 인구",
                format="%d명",
            ),
            "고령인구": st.column_config.NumberColumn(
                "65세 이상 인구",
                format="%d명",
            ),
            "고령화율": st.column_config.NumberColumn(
                "고령화율",
                format="%.2f%%",
            ),
        },
    )
