"""VisEval's own legality checks, ported verbatim from `microsoft/VisEval` (`viseval/check/{chart_check,data_check,order_check,
time_utils}.py`, MIT License, Copyright (c) Microsoft Corporation). The published rule for `chart`, `data` and `order`, so those
rows are theirs and not ours; the only edits are the imports, which now point at this one file. Do not tidy this file: a rewrite
would be a house rule wearing their name. What feeds it is ours and lives in `tests/bench/runner.py` (`chart_info`).

MIT License

Copyright (c) Microsoft Corporation.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation
files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy,
modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software
is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
"""

# ruff: noqa
# ---- time_utils.py ----------------------------------------------------------------------------------------------------------
from datetime import datetime

from dateutil import parser

TIME_MAP = {
    "mon": "monday",
    "tue": "tuesday",
    "wed": "wednesday",
    "thu": "thursday",
    "fri": "friday",
    "sat": "saturday",
    "sun": "sunday",
    "jan": "january",
    "feb": "february",
    "mar": "march",
    "apr": "april",
    "may": "may",
    "jun": "june",
    "jul": "july",
    "aug": "august",
    "sep": "september",
    "sept": "september",
    "oct": "october",
    "nov": "november",
    "dec": "december",
    "mon": "monday",
    "tue": "tuesday",
    "wed": "wednesday",
    "thu": "thursday",
    "thur": "thursday",
    "fri": "friday",
    "sat": "saturday",
    "sun": "sunday",
}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MONTHS = [
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
]


def is_month_or_weekday(s: str):
    if isinstance(s, str):
        if s.lower() in TIME_MAP or (
            s[0:3].lower() in TIME_MAP and s.lower() == TIME_MAP[s[0:3].lower()]
        ):
            return True
    return False


def convert_month_or_weekday_to_int(s: str) -> int:
    if is_month_or_weekday(s):
        if s[0:3].lower() in WEEKDAYS:
            return WEEKDAYS.index(s[0:3].lower()) + 1
        if s[0:3].lower() in MONTHS:
            return MONTHS.index(s[0:3].lower()) + 1
    return -1


def is_datetime(s):
    # consider month and weekday as nominal
    if is_month_or_weekday(s):
        return False
    try:
        parser.parse(s)
        return True
    except ValueError:
        return False


def check_time_format(time_str, time_format):
    try:
        datetime.strptime(time_str, time_format)
        return True
    except ValueError:
        return False


def parse_time_to_timestamp(time_str):
    # 0:00 is prone to bias
    if check_time_format(time_str, "%Y"):
        time_str = time_str + "-01-01 00:00:10"
    elif check_time_format(time_str, "%Y-%m"):
        time_str = time_str + "-01 00:00:10"
    elif check_time_format(time_str, "%Y-%m-%d"):
        time_str = time_str + " 00:00:10"

    try:
        parsed_time = parser.parse(time_str)
        timestamp = parsed_time.timestamp()
        return timestamp
    except Exception:
        return None


def parse_timestamp_to_time(timestamp):
    # todo: extract date format
    date_format = "%Y-%m-%d"
    try:
        parsed_time = datetime.fromtimestamp(timestamp)
        time_str = parsed_time.strftime(date_format)
        return time_str
    except Exception:
        return None


# handle case like 2008.436089
def parse_number_to_time(number):
    if number > 0 and number < 2999:
        timestamp = parse_time_to_timestamp(str(int(number)))
        timestamp += (number - int(number)) * 365 * 24 * 60 * 60
        return timestamp
    return number


def compare_time_strings(time_str1: str, time_str2: str):
    try:
        if parser.parse(time_str1).timestamp() == parser.parse(time_str2).timestamp():
            return True
    except Exception:
        pass

    try:
        str1 = TIME_MAP.get(time_str1.lower(), time_str1)
        str2 = TIME_MAP.get(time_str2.lower(), time_str2)

        if str1.lower() == str2.lower():
            return True

        if (
            is_month_or_weekday(str1)
            and str(convert_month_or_weekday_to_int(str1)) == str2
        ):
            return True
        if (
            is_month_or_weekday(str2)
            and str(convert_month_or_weekday_to_int(str2)) == str1
        ):
            return True

        if (
            is_month_or_weekday(str1)
            and str(convert_month_or_weekday_to_int(str1)) == str2
        ):
            return True
        if (
            is_month_or_weekday(str2)
            and str(convert_month_or_weekday_to_int(str2)) == str1
        ):
            return True

        if is_month_or_weekday(str2) and parse_time_to_timestamp(time_str1) is not None:
            # weekday
            if (
                str2.lower()
                == datetime.fromtimestamp(parse_time_to_timestamp(time_str1))
                .strftime("%A")
                .lower()
            ):
                return True
            # month
            if (
                str2.lower()
                == datetime.fromtimestamp(parse_time_to_timestamp(time_str1))
                .strftime("%B")
                .lower()
            ):
                return True
        if is_month_or_weekday(str1) and parse_time_to_timestamp(time_str2) is not None:
            # weekday
            if (
                str1.lower()
                == datetime.fromtimestamp(parse_time_to_timestamp(time_str2))
                .strftime("%A")
                .lower()
            ):
                return True
            # month
            if (
                str1.lower()
                == datetime.fromtimestamp(parse_time_to_timestamp(time_str2))
                .strftime("%B")
                .lower()
            ):
                return True

        return False
    except Exception:
        return False

# ---- chart_check.py ---------------------------------------------------------------------------------------------------------

def chart_check(
    chart_info: dict, chart_type_ground_truth: str, stacked_bar: bool = False
):
    # chart_type_ground_truth: pie, bar, line, scatter, stacked bar, grouping line, grouping scatter
    if "chart" not in chart_info:
        return False, "Cannot recognize the chart type."

    chart_type = chart_info["chart"]

    if chart_type_ground_truth.lower() in chart_type.lower() or (
        chart_type_ground_truth == "Stacked Bar"
        and not stacked_bar
        and "grouping bar" in chart_type.lower()
    ):
        return True, "Chart type is consistent with ground truth."

    return False, "Chart type is not consistent with ground truth."

# ---- data_check.py ----------------------------------------------------------------------------------------------------------
import copy
import json


PRECISION = 0.0005


def is_numeric(s):
    try:
        float(s)
        return True
    except ValueError:
        return False

def compare_string(string, ground_truth):
    return string.strip().lower().startswith(ground_truth.strip().lower())

def convert_ground_truth_data(ground_truth):
    # ground truth data
    x_data = ground_truth["x_data"]
    y_data = ground_truth["y_data"]
    classify = ground_truth["classify"]

    data_ground_truth = []
    if len(classify) == 0:
        x_data = x_data[0]
        y_data = y_data[0]
        for index in range(len(x_data)):
            data_ground_truth.append(
                {
                    "field_x": x_data[index],
                    "field_y": y_data[index],
                }
            )
    else:
        # scatter
        if len(classify) == len(x_data) and len(classify) == len(y_data):
            for index in range(len(classify)):
                for index2 in range(len(x_data[index])):
                    data_ground_truth.append(
                        {
                            "field_x": x_data[index][index2],
                            "field_y": y_data[index][index2],
                            "field_classify": classify[index],
                        }
                    )
        # line / bar
        elif len(classify) == len(y_data):
            for index in range(len(classify)):
                for index2 in range(len(x_data[0])):
                    data_ground_truth.append(
                        {
                            "field_x": x_data[0][index2],
                            "field_y": y_data[index][index2],
                            "field_classify": classify[index],
                        }
                    )
    return data_ground_truth


# return answer and rationale for the data check
def compare_data(data_ground_truth, chart_info):
    # deep copy: avoid to change origin data
    data = copy.deepcopy(chart_info["data"])
    encoding = chart_info["encoding"]
    # line chart may have different length of data because line chart might omit some values
    if (len(data) != len(data_ground_truth) and chart_info["mark"] != "line") or (
        len(data) > len(data_ground_truth)
    ):
        return (
            False,
            f"visualization data length {len(data)} != ground truth length {len(data_ground_truth)}.",
        )

    if chart_info["mark"] == "arc":
        # field_fill -> field_x
        field_x = "field_fill"
        # field_theta -> field_y relative
        field_y = "field_theta"

        scale = None
        for datum_ground_truth in data_ground_truth:
            datum = [
                x
                for x in data
                if compare_string(str(x[field_x]), str(datum_ground_truth["field_x"]))
            ]
            if len(datum) == 1:
                datum = datum[0]
                if scale is None:
                    scale = datum[field_y] / datum_ground_truth["field_y"]
                else:
                    if (
                        abs(datum[field_y] / datum_ground_truth["field_y"] - scale)
                        > PRECISION
                    ):
                        return False, f"{json.dumps(datum_ground_truth)} not found\n"
            elif len(datum) == 0:
                return False, f"{json.dumps(datum_ground_truth)} not found."
            elif len(datum) > 1:
                return False, f"{json.dumps(datum_ground_truth)} found more than one."
    else:
        if len(encoding.keys()) < len(data_ground_truth[0].keys()):
            return False, "too few channels\n"
        elif len(encoding.keys()) > 3:
            return False, "too many channels\n"
        elif len(encoding.keys()) > len(data_ground_truth[0].keys()):
            # only keep x and y for comparison
            encoding = {key: encoding[key] for key in ["x", "y"]}

        for datum_ground_truth in data_ground_truth:
            datum = data
            for key in encoding:
                if key == "x" or key == "y":
                    field = f"field_{key}"
                else:
                    field = "field_classify"
                if (
                    encoding[key]["type"] == "quantitative"
                    or encoding[key]["type"] == "temporal"
                ):
                    # e.g., Monday -> 1
                    if is_month_or_weekday(datum_ground_truth[field]):
                        if encoding[key]["type"] == "temporal":
                            datum = [
                                x
                                for x in datum
                                if compare_time_strings(
                                    x["field_" + key].strip(),
                                    str(datum_ground_truth[field]).strip(),
                                )
                            ]
                            value_ground_truth = None
                        else:
                            value_ground_truth = convert_month_or_weekday_to_int(
                                datum_ground_truth[field]
                            )
                    else:
                        try:
                            value_ground_truth = float(datum_ground_truth[field])
                        except Exception:
                            # not a number
                            # try temporal
                            try:
                                value_ground_truth = parse_time_to_timestamp(
                                    datum_ground_truth[field]
                                )
                                if value_ground_truth is None:
                                    return (
                                        False,
                                        f"The data type of {key}({encoding[key]['type']}) is wrong.",
                                    )

                                if encoding[key]["type"] == "quantitative":
                                    for x in datum:
                                        x["field_" + key] = parse_number_to_time(
                                            x["field_" + key]
                                        )
                            except Exception:
                                return (
                                    False,
                                    f"The data type of {key}({encoding[key]['type']}) is wrong.",
                                )

                    field_vis = (
                        "field_" + key
                        if encoding[key]["type"] != "temporal"
                        else "field_" + key + "_origin"
                    )
                    if value_ground_truth is not None:
                        # avoid division by zero
                        datum = [
                            x
                            for x in datum
                            if abs((x[field_vis] - value_ground_truth)) <= PRECISION
                            or (
                                value_ground_truth != 0
                                and abs(
                                    (x[field_vis] - value_ground_truth)
                                    / value_ground_truth
                                )
                                <= PRECISION
                            )
                        ]
                elif encoding[key]["type"] == "nominal":
                    # exact match or time match
                    datum = [
                        x
                        for x in datum
                        if compare_string(x["field_" + key], str(datum_ground_truth[field]))
                        or compare_time_strings(
                            x["field_" + key].strip(),
                            str(datum_ground_truth[field]).strip(),
                        )
                    ]

            if len(datum) == 0:
                if (
                    chart_info["mark"] == "line"
                    and (
                        encoding["x"]["type"] == "quantitative"
                        or encoding["x"]["type"] == "temporal"
                    )
                    and (encoding["y"]["type"] == "quantitative")
                ):
                    # use all data
                    datum = chart_info["data"]
                    if len(data_ground_truth[0].keys()) == 3:
                        datum = [
                            x
                            for x in datum
                            if x["field_stroke"].strip()
                            == datum_ground_truth["field_classify"].strip()
                        ]
                    # line chart might omit some values
                    min_larger_index = -1
                    max_smaller_index = -1
                    # convert
                    field_vis = (
                        "field_x"
                        if encoding["x"]["type"] != "temporal"
                        else "field_x_origin"
                    )
                    value_ground_truth = datum_ground_truth["field_x"]
                    try:
                        value_ground_truth = float(value_ground_truth)
                    except Exception:
                        value_ground_truth = parse_time_to_timestamp(value_ground_truth)

                    for index in range(len(datum)):
                        if datum[index][field_vis] > value_ground_truth:
                            if (
                                min_larger_index == -1
                                or datum[index][field_vis]
                                < datum[min_larger_index][field_vis]
                            ):
                                min_larger_index = index
                        elif datum[index][field_vis] < value_ground_truth:
                            if (
                                max_smaller_index == -1
                                or datum[index][field_vis]
                                > datum[max_smaller_index][field_vis]
                            ):
                                max_smaller_index = index
                    if min_larger_index != -1 and max_smaller_index != -1:
                        if (
                            abs(
                                (
                                    datum[min_larger_index]["field_y"]
                                    - datum[max_smaller_index]["field_y"]
                                )
                            )
                            <= PRECISION
                            or abs(
                                (
                                    datum[min_larger_index]["field_y"]
                                    - datum_ground_truth["field_y"]
                                )
                            )
                            <= PRECISION
                        ):
                            continue
                        else:
                            return (
                                False,
                                f"{json.dumps(datum_ground_truth)} not found\n",
                            )
                return False, f"{json.dumps(datum_ground_truth)} not found."
            else:
                data.remove(datum[0])

    return True, "The data on the charts is consistent with the ground truth."


def data_check(chart_info: dict, data: dict, channel_specified: list):
    if ("data" not in chart_info) or (len(chart_info["data"]) == 0):
        return False, "The data on the charts cannot be understood."

    data_ground_truth = convert_ground_truth_data(data)
    # filter zero data
    if chart_info["mark"] == "bar":
        data_ground_truth = list(
            filter(lambda x: x["field_x"] != 0 and x["field_y"] != 0, data_ground_truth)
        )
        chart_info["data"] = list(
            filter(
                lambda x: x["field_x"] != 0 and x["field_y"] != 0,
                chart_info["data"],
            )
        )
    candidates = [data_ground_truth]
    # ground truth channel -> chart channel
    channel_maps = []
    if len(data["classify"]) == 0:
        # 2 channels
        channel_maps.append({"x": "x", "y": "y"})
        if "x" not in channel_specified and "y" not in channel_specified:
            # swap x and y
            data_ground_truth_copy = copy.deepcopy(data_ground_truth)
            for datum in data_ground_truth_copy:
                datum["field_x"], datum["field_y"] = (
                    datum["field_y"],
                    datum["field_x"],
                )
            candidates.append(data_ground_truth_copy)
            channel_maps.append({"x": "y", "y": "x"})
    else:
        # 3 channels
        channel_maps.append({"x": "x", "y": "y", "classify": "classify"})
        channels = ["x", "y", "classify"]
        if len(channel_specified) <= 1:
            swap_channels = list(set(channels) - set(channel_specified))
            data_ground_truth_copy = copy.deepcopy(data_ground_truth)
            for datum in data_ground_truth_copy:
                (
                    datum[f"field_{swap_channels[0]}"],
                    datum[f"field_{swap_channels[1]}"],
                ) = (
                    datum[f"field_{swap_channels[1]}"],
                    datum[f"field_{swap_channels[0]}"],
                )
            candidates.append(data_ground_truth_copy)
            channel_map = {"x": "x", "y": "y", "classify": "classify"}
            channel_map[swap_channels[0]], channel_map[swap_channels[1]] = (
                channel_map[swap_channels[1]],
                channel_map[swap_channels[0]],
            )
            channel_maps.append(channel_map)
        if len(channel_specified) == 0:
            # swap x and z
            data_ground_truth_copy = copy.deepcopy(candidates[0])
            for datum in data_ground_truth_copy:
                datum["field_x"], datum["field_classify"] = (
                    datum["field_classify"],
                    datum["field_x"],
                )
            candidates.append(data_ground_truth_copy)
            channel_maps.append({"x": "classify", "y": "y", "classify": "x"})
            # swap x and y, x and z
            data_ground_truth_copy = copy.deepcopy(candidates[1])
            for datum in data_ground_truth_copy:
                datum["field_y"], datum["field_classify"] = (
                    datum["field_classify"],
                    datum["field_y"],
                )
            candidates.append(data_ground_truth_copy)
            channel_maps.append({"x": "y", "y": "classify", "classify": "x"})
    cache = None
    for i in range(len(candidates)):
        candidate = candidates[i]
        channel_map = channel_maps[i]
        answer, rationale = compare_data(candidate, chart_info)
        if answer:
            chart_info["channel_map"] = channel_map
            return answer, rationale
        if i == 0:
            cache = [answer, rationale]

    return cache[0], cache[1]

# ---- order_check.py ---------------------------------------------------------------------------------------------------------

def order_check(chart_info: dict, ground_truth: dict, sort_by: str):
    order = ground_truth["sort"]
    encoding = chart_info["encoding"]
    channel_map = chart_info["channel_map"]

    if order is not None:
        # bar, line
        if sort_by == "axis":
            order_channel = order["channel"]
        else:
            source_channel = order["channel"]
            if source_channel not in channel_map:
                return False, f"Missing {source_channel} channel mapping."
            order_channel = channel_map[source_channel]

        other_channel = "y" if order_channel == "x" else "x"

        for channel in (order_channel, other_channel):
            if channel not in encoding:
                return False, f"Missing {channel} encoding."
            if "scale" not in encoding[channel]:
                return False, f"Missing scale for {channel} encoding."

        if order_channel not in channel_map:
            return False, f"Missing {order_channel} channel mapping."

        order_channel_scale = encoding[order_channel]["scale"]
        other_channel_scale = encoding[other_channel]["scale"]
        data = chart_info["data"]

        # origin channel
        if (
            channel_map[order_channel] == "x"
            or channel_map[order_channel] == "classify"
        ):
            arr = []
            if len(order_channel_scale["range"]) == 0:
                scale_range = range(1, 1 + len(order_channel_scale["domain"]))
            else:
                scale_range = order_channel_scale["range"]

            for index in range(len(order_channel_scale["domain"])):
                arr.append(
                    tuple(
                        [
                            order_channel_scale["domain"][index],
                            scale_range[index],
                        ]
                    )
                )
            if order_channel == "x":
                reverse = True
            else:
                reverse = False
            if order["order"] == "ascending":
                arr.sort(key=lambda x: x[0], reverse=reverse)
            elif order["order"] == "descending":
                arr.sort(key=lambda x: x[0], reverse=not reverse)
            else:  # custom order
                sort_order = {}
                for index in range(len(order["order"])):
                    sort_order[order["order"][index]] = index
                arr.sort(key=lambda x: sort_order[x[0]], reverse=reverse)

            is_sorted = all([arr[i][1] > arr[i + 1][1] for i in range(len(arr) - 1)])
        # 'quantitative'
        else:
            # sort by other channel
            values_other = []
            if (
                "type" not in other_channel_scale
                or other_channel_scale["type"] == "ordinal"
            ):
                for index in range(len(other_channel_scale["domain"])):
                    values_other.append(
                        tuple(
                            [
                                other_channel_scale["domain"][index],
                                other_channel_scale["range"][index],
                            ]
                        )
                    )
                values_other.sort(key=lambda x: x[1])
                values_other = [item[0] for item in values_other]
            else:
                values_other = list(
                    set([datum["field_" + other_channel] for datum in data])
                )
                values_other.sort(
                    reverse=True
                    if (
                        other_channel_scale["domain"][1]
                        - other_channel_scale["domain"][0]
                    )
                    / (
                        other_channel_scale["range"][1]
                        - other_channel_scale["range"][0]
                    )
                    < 0
                    else False
                )

            # cumulative
            values_order = []
            for value in values_other:
                data_filter = [
                    float(d["field_" + order_channel])
                    for d in data
                    if d["field_" + other_channel] == value
                ]
                values_order.append(sum(data_filter))

            # filter zero data
            if chart_info["mark"] == "bar":
                values_order = list(filter(lambda x: x != 0, values_order))

            is_sorted = True
            if order["order"] == "ascending":
                is_sorted = all(
                    [
                        values_order[i] <= values_order[i + 1]
                        for i in range(len(values_order) - 1)
                    ]
                )
            elif order["order"] == "descending":
                is_sorted = all(
                    [
                        values_order[i] >= values_order[i + 1]
                        for i in range(len(values_order) - 1)
                    ]
                )

        if not is_sorted:
            return False, "Doesn't sort."
        else:
            return True, "Sorted."
    else:
        return True, "No sort."
