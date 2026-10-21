# 생성한 원본 목록

`scripts/profile_source_data.py`로 재생성합니다. 이 폴더의 CSV는 검토 자료이며 실행 시 사용하는 승인 매핑이 아닙니다.

| 파일 | 내용 |
|---|---|
| [raw-sdr-columns.csv](raw-sdr-columns.csv) | 원본 84,989건의 76컬럼 이름·순서·빈 값 제외 건수·예시. 예시는 서로 다른 행일 수 있음 |
| [operator-inventory.csv](operator-inventory.csv) | 검색용 SDR의 운항사 코드 24개. service_airline_code는 미확정 |
| [model-inventory.csv](model-inventory.csv) | 검색용 모델 키 38개와 원본 표기. service_aircraft_model은 미확정 |
| [scope-coverage.csv](scope-coverage.csv) | 운항사×정확 모델 100조합의 SDR·AMM 참조·청크 건수 |

원본 전체 84,989건의 코드/모델 종류 수와 검색용 71,741건의 종류 수는 다릅니다. 원본 전체에는 운항사 33개(빈 값 제외), 모델 49개(빈 값 제외)가 관측되며 검색 목록은 검색 적격·평가 분리를 거친 결과입니다.

빈 값이 아닌 UNKNOWN/NONE/NA/UNK도 raw-sdr-columns의 nonempty_count에 포함됩니다. examples_json은 관측값 예시이지 유효값 허용 목록이 아닙니다. 코드·시간·수치의 단위나 공식 의미를 이 파일만으로 확정하지 않습니다.

승인된 항공사·모델 대응값은 별도 Backend 소유 자료로 관리합니다. 이 생성 CSV에 수동 입력하면 재실행 때 덮어써집니다. 검증 기준은 [데이터 매핑](../DATA_MAPPING.md), 팀 질문은 [논의 목록](../TEAM_DISCUSSION.md)을 참조하세요.
