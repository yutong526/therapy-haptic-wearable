import numpy as np
import time
from collections import deque


class HRCalculator:
    def __init__(self, sample_rate=500):
        """
        中值滑动平均滤波心率计算器

        参数:
            sample_rate: 采样率(Hz)，默认为500Hz
        """
        self.sample_rate = sample_rate
        self.last_state = None
        self.edge_times = deque(maxlen=2)  # 保存最近的2个下降沿时间点
        self.edge_vals = deque(maxlen=4)   # 保存最近的4个上或下降沿时间点的值
        self.sample_count = 0

        # 心率计算相关
        self.raw_hr = None  # 原始计算心率
        self.filtered_hr = None  # 滤波后心率
        self.hr_history = deque(maxlen=5)  # 心率历史记录（用于滤波）
        self.rr_intervals = deque(maxlen=5)  # RR间期历史记录

        # 滤波参数
        self.hr_window = 6  # 心率数据滤波窗口大小
        self.min_hr = 40  # 最低合理心率(bpm)
        self.max_hr = 180  # 最高合理心率(bpm)
        self.max_hr_change = 20  # 最大允许心率变化(bpm/beat)

    def middle_segment_average(arr, middle_ratio=0.6):
        """
        计算排序后数组中间一段的平均值

        参数:
        arr: 一维数组
        middle_ratio: 中间部分的比例(默认取中间60%)

        返回:
        中间段的平均值
        """
        if not arr:
            return None

        # 对数组进行排序
        sorted_arr = sorted(arr)

        # 计算中间段的起始和结束索引
        n = len(sorted_arr)
        skip = int((1 - middle_ratio) * n / 2)
        start = skip
        end = n - skip

        # 获取中间段数据
        middle_segment = sorted_arr[start:end]

        # 计算平均值
        average = sum(middle_segment) / len(middle_segment)

        return average

    def _median_moving_average_filter(self, hr):
        """
        中值滑动平均混合滤波

        参数:
            hr: 原始计算的心率值

        返回:
            滤波后的心率值或None(如果心率无效)
        """
        if hr is None:
            return None

        # 1. 基础范围检查
        if hr < self.min_hr or hr > self.max_hr:
            return None

        # 2. 添加到历史记录
        self.hr_history.append(hr)

        # 3. 检查是否有足够数据
        if len(self.hr_history) < self.hr_window:
            return hr  # 数据不足时返回原始值

        # 4. 中值-滑动平均混合滤波
        recent_hr = list(self.hr_history)

        # 5. 计算中间段平均值
        filtered_hr = self.middle_segment_average(recent_hr)

        # 6. 变化率检查
        if self.filtered_hr is not None and filtered_hr is not None:
            if abs(filtered_hr - self.filtered_hr) > self.max_hr_change:
                # 变化过大，使用加权平均
                filtered_hr = self.filtered_hr * 0.7 + filtered_hr * 0.3

        return filtered_hr

    def _rr_interval_check(self, rr_interval):
        """
        RR间期一致性检查

        参数:
            rr_interval: 当前RR间期

        返回:
            修正后的RR间期或None(如果无效)
        """
        if rr_interval <= 0:
            return None

        self.rr_intervals.append(rr_interval)

        if len(self.rr_intervals) >= 3:
            # 计算中值RR间期
            median_rr = np.median(self.rr_intervals)

            # 检查当前RR间期是否异常
            if abs(rr_interval - median_rr) > median_rr * 0.3:  # 偏差超过30%
                return median_rr  # 使用中值替代

        return rr_interval

    def process_samples(self, a_data,s_data):
        """
        处理输入的样本数据，检测下降沿并计算滤波后的心率

        参数:
            samples: 包含0和1的一维数组

        返回:
            如果检测到有效心率则返回滤波后的心率值(bpm)，否则返回None
        """

        filtered_hr = None

        # for sample in s_data:
        for n in range(0,len(s_data)) :

            current_state = s_data[n]

            # 检测上降沿(0 -> 1)
            if self.last_state == 0 and current_state == 1:
                current_time = self.sample_count / self.sample_rate
                self.edge_times.append(current_time)
                self.edge_vals.append(a_data[n])

            # 检测下降沿(0 -> 1)
            elif self.last_state == 1 and current_state == 0:
                self.edge_vals.append(a_data[n])

                if len(self.edge_vals) >= 4:

                    diff_val_1 = self.edge_vals[1] - self.edge_vals[0]
                    diff_val_2 = self.edge_vals[3] - self.edge_vals[2]
                    # self.edge_vals.remove()
                    self.edge_vals.popleft()
                    self.edge_vals.popleft()
                    # print(type(self.edge_vals))

                    # 当有两个时间点时计算心率
                    if len(self.edge_times) >= 2 :
                        if diff_val_1 > 500 and diff_val_2 > 500 :
                            rr_interval = self.edge_times[-1] - self.edge_times[-2]
                            # RR间期检查
                            valid_rr = self._rr_interval_check(rr_interval)
                            if valid_rr is not None:
                                self.raw_hr = 60 / valid_rr
                                # 应用中值滑动平均滤波
                                self.filtered_hr = self._median_moving_average_filter(self.raw_hr)
                                filtered_hr = self.filtered_hr

                    self.edge_times.popleft()

            self.last_state = current_state
            self.sample_count += 1

        return filtered_hr

    def get_current_hr(self):
        """
        获取当前心率值（经过滤波处理）

        返回:
            当前心率估计值（优先顺序：最新滤波值 > 历史中值 > None）
        """
        if self.filtered_hr is not None:
            return self.filtered_hr

        if len(self.hr_history) > 0:
            # 数据不足时返回历史中值
            return np.median(list(self.hr_history)[-self.hr_window:])

        return None