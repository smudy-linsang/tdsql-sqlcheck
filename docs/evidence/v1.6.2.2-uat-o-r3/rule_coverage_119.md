# 119 条核心规则实测账本（第三轮）

被测提交 `1596e8b`；主版本 sqlglot 30.14.0。依据 `rule_probe_current.json` 1000 条和 `supplemental_core.json` 前五条。

注册 119 条，至少命中 116 条；未证明有效：R025, R038, R049。命中过不代表全边界通过，R042 本轮已有反例。

107 条有非注入元数据输入命中；7 条原有元数据分支加本轮 R035/R059，共9条仅用合成上下文验证。真实 TDSQL 在线元数据供给不据此签字。

| 规则 | 本轮命中次数 | 首个证据 ID | 验收边界 |
|---|---:|---|---|
| R001 | 5 | corpus:01_naming_ddl.sql:R001_01:distributed | 有样本命中，不等于全语义证明 |
| R002 | 2 | corpus:01_naming_ddl.sql:R002_01:distributed | 有样本命中，不等于全语义证明 |
| R003 | 29 | corpus:01_naming_ddl.sql:DDL_MULTI_01:distributed | 有样本命中，不等于全语义证明 |
| R004 | 30 | corpus:01_naming_ddl.sql:DDL_MULTI_01:distributed | 有样本命中，不等于全语义证明 |
| R005 | 30 | corpus:01_naming_ddl.sql:DDL_MULTI_01:distributed | 有样本命中，不等于全语义证明 |
| R006 | 2 | corpus:01_naming_ddl.sql:R006_01:distributed | 有样本命中，不等于全语义证明 |
| R007 | 2 | corpus:01_naming_ddl.sql:R007_01:distributed | 有样本命中，不等于全语义证明 |
| R008 | 2 | corpus:01_naming_ddl.sql:R008_01:distributed | 有样本命中，不等于全语义证明 |
| R009 | 2 | corpus:01_naming_ddl.sql:R009_01:distributed | 有样本命中，不等于全语义证明 |
| R010 | 2 | corpus:01_naming_ddl.sql:R010_01:distributed | 有样本命中，不等于全语义证明 |
| R011 | 152 | corpus:01_naming_ddl.sql:R011_01:distributed | 有样本命中，不等于全语义证明 |
| R012 | 3 | corpus:02_dml_perf_sec_txn.sql:R012_01:distributed | 有样本命中，不等于全语义证明 |
| R013 | 8 | corpus:02_dml_perf_sec_txn.sql:R013_R014_01:distributed | 有样本命中，不等于全语义证明 |
| R014 | 8 | corpus:02_dml_perf_sec_txn.sql:R013_R014_01:distributed | 有样本命中，不等于全语义证明 |
| R015 | 2 | corpus:02_dml_perf_sec_txn.sql:R015_01:distributed | 有样本命中，不等于全语义证明 |
| R016 | 11 | corpus:02_dml_perf_sec_txn.sql:R016_01:distributed | 有样本命中，不等于全语义证明 |
| R017 | 2 | corpus:02_dml_perf_sec_txn.sql:R017_01:distributed | 有样本命中，不等于全语义证明 |
| R018 | 4 | corpus:03_index.sql:R018_01:distributed | 有样本命中，不等于全语义证明 |
| R019 | 4 | corpus:03_index.sql:R019_01:distributed | 有样本命中，不等于全语义证明 |
| R020 | 4 | corpus:02_dml_perf_sec_txn.sql:R015_01:distributed | 有样本命中，不等于全语义证明 |
| R021 | 1 | corpus:02_dml_perf_sec_txn.sql:R021_01:distributed | 有样本命中，不等于全语义证明 |
| R022 | 1 | corpus:02_dml_perf_sec_txn.sql:R022_01:distributed | 有样本命中，不等于全语义证明 |
| R023 | 1 | corpus:01_naming_ddl.sql:R023_01:distributed | 有样本命中，不等于全语义证明 |
| R024 | 1 | corpus:01_naming_ddl.sql:R024_01:distributed | 有样本命中，不等于全语义证明 |
| R025 | 0 | — | ALTER 动作供给未覆盖；本例未触发 |
| R026 | 2 | corpus:01_naming_ddl.sql:R026_01:distributed | 有样本命中，不等于全语义证明 |
| R027 | 2 | corpus:01_naming_ddl.sql:R027_01:distributed | 有样本命中，不等于全语义证明 |
| R028 | 32 | corpus:01_naming_ddl.sql:DDL_MULTI_01:distributed | 有样本命中，不等于全语义证明 |
| R029 | 43 | corpus:01_naming_ddl.sql:DDL_MULTI_01:distributed | 有样本命中，不等于全语义证明 |
| R030 | 16 | corpus:01_naming_ddl.sql:R030_R031_01:distributed | 有样本命中，不等于全语义证明 |
| R031 | 5 | corpus:01_naming_ddl.sql:R030_R031_01:distributed | 有样本命中，不等于全语义证明 |
| R032 | 1 | corpus:01_naming_ddl.sql:R024_01:distributed | 有样本命中，不等于全语义证明 |
| R033 | 6 | corpus:01_naming_ddl.sql:R007_01:distributed | 有样本命中，不等于全语义证明 |
| R034 | 2 | corpus:01_naming_ddl.sql:R034_01:distributed | 有样本命中，不等于全语义证明 |
| R035 | 1 | supplemental:R035 | 合成元数据分支，未验证真实在线供给 |
| R036 | 751 | corpus:01_naming_ddl.sql:DDL_MULTI_01:distributed | 有样本命中，不等于全语义证明 |
| R037 | 751 | corpus:01_naming_ddl.sql:DDL_MULTI_01:distributed | 有样本命中，不等于全语义证明 |
| R038 | 0 | — | raw_type 未包含 AUTO_INCREMENT；本例未触发 |
| R039 | 1 | corpus:02_dml_perf_sec_txn.sql:R039_01:distributed | 有样本命中，不等于全语义证明 |
| R040 | 2 | corpus:02_dml_perf_sec_txn.sql:R040_01:distributed | 有样本命中，不等于全语义证明 |
| R041 | 2 | corpus:02_dml_perf_sec_txn.sql:R041_01:distributed | 有样本命中，不等于全语义证明 |
| R042 | 2 | corpus:02_dml_perf_sec_txn.sql:R042_01:distributed | BLOCK：有正例命中，但 # 注释中的引号造成新增漏报/误通过 |
| R043 | 1 | corpus:02_dml_perf_sec_txn.sql:R043_01:distributed | 有样本命中，不等于全语义证明 |
| R044 | 2 | corpus:02_dml_perf_sec_txn.sql:R044_01:distributed | 有样本命中，不等于全语义证明 |
| R045 | 2 | corpus:02_dml_perf_sec_txn.sql:R045_01:distributed | 有样本命中，不等于全语义证明 |
| R046 | 2 | corpus:02_dml_perf_sec_txn.sql:R046_01:distributed | 有样本命中，不等于全语义证明 |
| R047 | 2 | corpus:02_dml_perf_sec_txn.sql:R047_01:distributed | 有样本命中，不等于全语义证明 |
| R048 | 2 | metadata:R048 | 合成元数据分支，未验证真实在线供给 |
| R049 | 0 | — | 当前实现占位返回 None |
| R050 | 2 | corpus:02_dml_perf_sec_txn.sql:R050_01:distributed | 有样本命中，不等于全语义证明 |
| R051 | 19 | corpus:02_dml_perf_sec_txn.sql:R039_01:distributed | 有样本命中，不等于全语义证明 |
| R052 | 2 | corpus:02_dml_perf_sec_txn.sql:R052_01:distributed | 有样本命中，不等于全语义证明 |
| R053 | 6 | corpus:02_dml_perf_sec_txn.sql:R020_01:distributed | 有样本命中，不等于全语义证明 |
| R054 | 134 | corpus:01_naming_ddl.sql:R118_01:distributed | 有样本命中，不等于全语义证明 |
| R055 | 2 | metadata:R055 | 合成元数据分支，未验证真实在线供给 |
| R056 | 1 | metadata:R056 | 合成元数据分支，未验证真实在线供给 |
| R057 | 2 | metadata:R048 | 合成元数据分支，未验证真实在线供给 |
| R058 | 1 | metadata:R058 | 合成元数据分支，未验证真实在线供给 |
| R059 | 1 | supplemental:R059 | 合成元数据分支，未验证真实在线供给 |
| R060 | 2 | metadata:R055 | 合成元数据分支，未验证真实在线供给 |
| R077 | 170 | corpus:01_naming_ddl.sql:R001_01:distributed | 有样本命中，不等于全语义证明 |
| R061 | 8 | corpus:03_index.sql:R061_01:distributed | 有样本命中，不等于全语义证明 |
| R062 | 2 | corpus:03_index.sql:R062_01:distributed | 有样本命中，不等于全语义证明 |
| R063 | 4 | corpus:03_index.sql:R063_01:distributed | 有样本命中，不等于全语义证明 |
| R064 | 2 | metadata:R056 | 合成元数据分支，未验证真实在线供给 |
| R065 | 4 | corpus:03_index.sql:R065_01:distributed | 有样本命中，不等于全语义证明 |
| R066 | 50 | corpus:03_index.sql:R066_01:distributed | 有样本命中，不等于全语义证明 |
| R067 | 6 | corpus:03_index.sql:R067_01:distributed | 有样本命中，不等于全语义证明 |
| R068 | 9 | corpus:02_dml_perf_sec_txn.sql:R020_01:distributed | 有样本命中，不等于全语义证明 |
| R069 | 5 | corpus:02_dml_perf_sec_txn.sql:R059_01:distributed | 有样本命中，不等于全语义证明 |
| R070 | 8 | corpus:02_dml_perf_sec_txn.sql:R013_R014_01:distributed | 有样本命中，不等于全语义证明 |
| R071 | 5 | corpus:02_dml_perf_sec_txn.sql:R059_01:distributed | 有样本命中，不等于全语义证明 |
| R072 | 2 | corpus:02_dml_perf_sec_txn.sql:R072_01:distributed | 有样本命中，不等于全语义证明 |
| R073 | 4 | corpus:01_naming_ddl.sql:R026_01:distributed | 有样本命中，不等于全语义证明 |
| R074 | 2 | corpus:02_dml_perf_sec_txn.sql:R074_01:distributed | 有样本命中，不等于全语义证明 |
| R075 | 2 | corpus:02_dml_perf_sec_txn.sql:R075_01:distributed | 有样本命中，不等于全语义证明 |
| R076 | 2 | corpus:02_dml_perf_sec_txn.sql:R076_01:distributed | 有样本命中，不等于全语义证明 |
| R078 | 2 | corpus:01_naming_ddl.sql:R078_01:distributed | 有样本命中，不等于全语义证明 |
| R079 | 2 | corpus:05_oracle_compat.sql:R079_01:distributed | 有样本命中，不等于全语义证明 |
| R080 | 2 | corpus:05_oracle_compat.sql:R080_01:distributed | 有样本命中，不等于全语义证明 |
| R081 | 2 | corpus:05_oracle_compat.sql:R081_01:distributed | 有样本命中，不等于全语义证明 |
| R082 | 2 | corpus:05_oracle_compat.sql:R082_01:distributed | 有样本命中，不等于全语义证明 |
| R083 | 2 | corpus:05_oracle_compat.sql:R083_01:distributed | 有样本命中，不等于全语义证明 |
| R084 | 2 | corpus:02_dml_perf_sec_txn.sql:R084_01:distributed | 有样本命中，不等于全语义证明 |
| R085 | 2 | corpus:05_oracle_compat.sql:R085_01:distributed | 有样本命中，不等于全语义证明 |
| R086 | 2 | corpus:05_oracle_compat.sql:R086_01:distributed | 有样本命中，不等于全语义证明 |
| R087 | 2 | corpus:05_oracle_compat.sql:R087_01:distributed | 有样本命中，不等于全语义证明 |
| R088 | 2 | corpus:05_oracle_compat.sql:R088_01:distributed | 有样本命中，不等于全语义证明 |
| R089 | 2 | corpus:05_oracle_compat.sql:R089_01:distributed | 有样本命中，不等于全语义证明 |
| R090 | 2 | corpus:05_oracle_compat.sql:R090_01:distributed | 有样本命中，不等于全语义证明 |
| R091 | 2 | corpus:05_oracle_compat.sql:R091_01:distributed | 有样本命中，不等于全语义证明 |
| R092 | 1 | corpus:02_dml_perf_sec_txn.sql:R092_01:distributed | 有样本命中，不等于全语义证明 |
| R093 | 2 | corpus:05_oracle_compat.sql:R093_01:distributed | 有样本命中，不等于全语义证明 |
| R094 | 2 | corpus:05_oracle_compat.sql:R094_01:distributed | 有样本命中，不等于全语义证明 |
| R095 | 1 | corpus:02_dml_perf_sec_txn.sql:R095_01:distributed | 有样本命中，不等于全语义证明 |
| R096 | 2 | corpus:02_dml_perf_sec_txn.sql:R096_01:distributed | 有样本命中，不等于全语义证明 |
| R097 | 1 | corpus:01_naming_ddl.sql:R097_01:distributed | 有样本命中，不等于全语义证明 |
| R098 | 1 | corpus:01_naming_ddl.sql:R098_01:distributed | 有样本命中，不等于全语义证明 |
| R099 | 2 | corpus:05_oracle_compat.sql:R099_01:distributed | 有样本命中，不等于全语义证明 |
| R100 | 1 | corpus:02_dml_perf_sec_txn.sql:R100_01:distributed | 有样本命中，不等于全语义证明 |
| R101 | 2 | corpus:05_oracle_compat.sql:R101_01:distributed | 有样本命中，不等于全语义证明 |
| R102 | 2 | corpus:05_oracle_compat.sql:R102_01:distributed | 有样本命中，不等于全语义证明 |
| R103 | 2 | corpus:05_oracle_compat.sql:R103_01:distributed | 有样本命中，不等于全语义证明 |
| R104 | 20 | corpus:05_oracle_compat.sql:R104_01:distributed | 有样本命中，不等于全语义证明 |
| R105 | 2 | corpus:05_oracle_compat.sql:R105_01:distributed | 有样本命中，不等于全语义证明 |
| R106 | 2 | corpus:05_oracle_compat.sql:R106_01:distributed | 有样本命中，不等于全语义证明 |
| R107 | 1 | corpus:02_dml_perf_sec_txn.sql:R107_01:distributed | 有样本命中，不等于全语义证明 |
| R108 | 2 | corpus:05_oracle_compat.sql:R108_01:distributed | 有样本命中，不等于全语义证明 |
| R109 | 2 | corpus:02_dml_perf_sec_txn.sql:R109_01:distributed | 有样本命中，不等于全语义证明 |
| R110 | 2 | corpus:05_oracle_compat.sql:R110_01:distributed | 有样本命中，不等于全语义证明 |
| R111 | 1 | corpus:05_oracle_compat.sql:R111_01:distributed | 有样本命中，不等于全语义证明 |
| R112 | 1 | corpus:05_oracle_compat.sql:R112_01:distributed | 有样本命中，不等于全语义证明 |
| R113 | 1 | corpus:05_oracle_compat.sql:R113_01:distributed | 有样本命中，不等于全语义证明 |
| R114 | 2 | corpus:02_dml_perf_sec_txn.sql:R114_01:distributed | 有样本命中，不等于全语义证明 |
| R115 | 1 | corpus:01_naming_ddl.sql:R115_01:distributed | 有样本命中，不等于全语义证明 |
| R116 | 1 | corpus:01_naming_ddl.sql:R116_01:distributed | 有样本命中，不等于全语义证明 |
| R117 | 1 | corpus:01_naming_ddl.sql:R117_01:distributed | 有样本命中，不等于全语义证明 |
| R118 | 1 | corpus:01_naming_ddl.sql:R118_01:distributed | 有样本命中，不等于全语义证明 |
| R119 | 1 | corpus:05_oracle_compat.sql:R119_01:distributed | 有样本命中，不等于全语义证明 |
