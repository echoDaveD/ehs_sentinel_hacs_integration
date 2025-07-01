DOMAIN = "ehs_sentinel"
DEVICE_ID = "samsung_ehssentinel"
PLATFORM_SWITCH = "switch"
PLATFORM_SENSOR = "sensor"
PLATFORM_NUMBER = "number"
PLATFORM_BINARY_SENSOR = "binary_sensor"
PLATFORM_SELECT = "select"
PLATFORM_OPTIONS = "options"
DEFAULT_POLLING_YAML = """
fetch_interval: 
  - name: fsv10xx
    enable: false
    schedule: 30m
  - name: fsv20xx
    enable: false
    schedule: 30m
  - name: fsv30xx
    enable: false
    schedule: 30m
  - name: fsv40xx
    enable: false
    schedule: 30m
  - name: fsv50xx
    enable: false
    schedule: 30m
groups:
  fsv10xx:
    - VAR_IN_FSV_1011
    - VAR_IN_FSV_1012
    - VAR_IN_FSV_1021
    - VAR_IN_FSV_1022
    - VAR_IN_FSV_1031
    - VAR_IN_FSV_1032
    - VAR_IN_FSV_1041
    - VAR_IN_FSV_1042
    - VAR_IN_FSV_1051
    - VAR_IN_FSV_1052
  fsv20xx:
    - VAR_IN_FSV_2011
    - VAR_IN_FSV_2012
    - VAR_IN_FSV_2021
    - VAR_IN_FSV_2022
    - VAR_IN_FSV_2031
    - VAR_IN_FSV_2032
    - ENUM_IN_FSV_2041
    - VAR_IN_FSV_2051
    - VAR_IN_FSV_2052
    - VAR_IN_FSV_2061
    - VAR_IN_FSV_2062
    - VAR_IN_FSV_2071
    - VAR_IN_FSV_2072
    - ENUM_IN_FSV_2081
    - ENUM_IN_FSV_2091
    - ENUM_IN_FSV_2092
    - ENUM_IN_FSV_2093
    - ENUM_IN_FSV_2094
  fsv30xx:
    - ENUM_IN_FSV_3011
    - VAR_IN_FSV_3021
    - VAR_IN_FSV_3022
    - VAR_IN_FSV_3023
    - VAR_IN_FSV_3024
    - VAR_IN_FSV_3025
    - VAR_IN_FSV_3026
    - ENUM_IN_FSV_3031
    - VAR_IN_FSV_3032
    - VAR_IN_FSV_3033
    - ENUM_IN_FSV_3041
    - ENUM_IN_FSV_3042
    - VAR_IN_FSV_3043
    - VAR_IN_FSV_3044
    - VAR_IN_FSV_3045
    - VAR_IN_FSV_3046
    - ENUM_IN_FSV_3051
    - VAR_IN_FSV_3052
    - ENUM_IN_FSV_3061
    - ENUM_IN_FSV_3071
    - VAR_IN_FSV_3081
    - VAR_IN_FSV_3082
    - VAR_IN_FSV_3083
  fsv40xx:
    - ENUM_IN_FSV_4011
    - VAR_IN_FSV_4012
    - VAR_IN_FSV_4013
    - ENUM_IN_FSV_4021
    - ENUM_IN_FSV_4022
    - ENUM_IN_FSV_4023
    - VAR_IN_FSV_4024
    - VAR_IN_FSV_4025
    - ENUM_IN_FSV_4031
    - ENUM_IN_FSV_4032
    - VAR_IN_FSV_4033
    - ENUM_IN_FSV_4041
    - VAR_IN_FSV_4042
    - VAR_IN_FSV_4043
    - ENUM_IN_FSV_4044
    - VAR_IN_FSV_4045
    - VAR_IN_FSV_4046
    - ENUM_IN_FSV_4051
    - VAR_IN_FSV_4052
    - ENUM_IN_FSV_4053
    - ENUM_IN_FSV_4061
  fsv50xx:
    - VAR_IN_FSV_5011
    - VAR_IN_FSV_5012
    - VAR_IN_FSV_5013
    - VAR_IN_FSV_5014
    - VAR_IN_FSV_5015
    - VAR_IN_FSV_5016
    - VAR_IN_FSV_5017
    - VAR_IN_FSV_5018
    - VAR_IN_FSV_5019
    - VAR_IN_FSV_5021
    - VAR_IN_FSV_5031
    - ENUM_IN_FSV_5022
    - VAR_IN_FSV_5023
    - ENUM_IN_FSV_5041
    - ENUM_IN_FSV_5042
    - ENUM_IN_FSV_5043
    - ENUM_IN_FSV_5051
    - ENUM_IN_FSV_5061
    - ENUM_IN_FSV_5081
    - VAR_IN_FSV_5082
    - VAR_IN_FSV_5083
    - ENUM_IN_FSV_5091
    - VAR_IN_FSV_5092
    - VAR_IN_FSV_5093
    - ENUM_IN_FSV_5094
"""