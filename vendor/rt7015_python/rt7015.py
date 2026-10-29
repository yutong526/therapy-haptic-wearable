import struct
import numpy as np
from hr_calculator import HRCalculator

# 数据解析类
class RT7015Data:
    def __init__(self):

        self.rx_buff = []   # 数据包接收缓存区
        self.a_out_data = []  # 脉博模拟信号
        self.s_out_data = []  # 脉博方波信号
        self.s_hr_data = []  # 心率数据
        self.a_hr_data = []  # 心率数据

        self.sample_rate = 500  # 采样率
        self.signal_type = "PPG"

        # 使用方波信号计算心率
        self.hr_calculator = HRCalculator(self.sample_rate)  # 心率算法

    def set_signal_type(self, type):
        self.signal_type = type

    def clear(self):
        self.rx_buff = []
        self.a_out_data = []
        self.s_out_data = []
        self.s_hr_data = []
        self.a_hr_data = []

    # 解包函数
    def parse_data(self, data_in):

        self.rx_buff += data_in
        data = (self.rx_buff)

        while len(data) > 6:

            checkByte = data[1] ^ data[2]

            # 帧头校验
            if (data[0] == 0xA5) and (data[3] == checkByte):

                # 帧长度
                framLen = data[1] + 3

                # 判断剩于数据长度是否大于帧长度
                if framLen <= len(data):

                    # 校验帧尾
                    if data[framLen - 1] == 0x5A:
                        self.frame_unpack(bytes(data[0:framLen]))
                        del data[0:framLen]
                    else:
                        data.pop(0)
                        print("校验帧尾出错!")
                else:
                    break
            else:
                data.pop(0)
                print("校验帧头出错!")

        self.rx_buff = data

    def frame_unpack(self, data):

        # 帧地址
        frameType = data[2]

        if frameType == RT7015Cmd.ADDRESS_SAMPLE_PAR:
            sample_rates = [125,250,500,1000,2000]
            self.sample_rate = sample_rates[data[4]]

        # 原始信号数据帧
        if frameType == RT7015Cmd.ADDRESS_START:
            data_num = int((data[1] - 2) / 2)
            temp = data[4:(4 + data_num * 2)]
            data_temp = struct.unpack("H" * (len(temp) // 2), temp)

            a_out = np.array(data_temp[::2]).astype(float)/10
            s_out = np.array(data_temp[1::2]).astype(float)/10
            self.a_out_data += tuple(a_out)
            self.s_out_data += tuple(s_out)

            # 将大于1500的值置1，其他置0
            s_out_ = (s_out > 1500).astype(int)
            s_hr = self.hr_calculator.process_samples(a_out,s_out_)
            if s_hr is not None :
                # 显示结果
                print("s_hr: " + str(s_hr))
                self.s_hr_data.append(s_hr)

# 指令集
class RT7015Cmd:
    ADDRESS_HW_VERSION = 0x01  #硬件版本
    ADDRESS_SOFTWARE = 0x02  #软件版本
    ADDRESS_DEVICE_NAME = 0x03  #设备名称
    ADDRESS_DEVICE_MAC = 0x04  # MAC地址
    ADDRESS_POWER = 0x05  #电量信息
    ADDRESS_RESET = 0x06 # 复位
    ADDRESS_SAMPLE_PAR = 0x10 # 采样参数
    ADDRESS_START = 0x11  #开始/停止采集指令

    @staticmethod
    def cmd_data_pack(addr, is_write, data):

        cmd = []

        # 帧头
        cmd.append(0xAA)
        # 帧长度
        cmd.append(len(data) + 3)
        if is_write:
            cmd.append(0x80)  # 写指令
        else:
            cmd.append(0x81)  # 读指令
        # 地址
        cmd.append(addr)

        # 数据内容
        cmd += data

        # 校验码（待定）
        cmd.append(0x00)

        # 帧尾
        cmd.append(0xBB)

        # 计算校验码
        xor = 0x00
        for b in cmd[1:]:
            xor ^= b
        cmd[len(cmd) - 2] = xor
        return cmd

    # 开始采集指令
    @staticmethod
    def start_collect_cmd():
        data = []
        data.append(0x01)
        return RT7015Cmd.cmd_data_pack(RT7015Cmd.ADDRESS_START, True, data)

    # 停止采集指令
    @staticmethod
    def stop_collect_cmd():
        data = []
        data.append(0x00)
        return RT7015Cmd.cmd_data_pack(RT7015Cmd.ADDRESS_START, True, data)
