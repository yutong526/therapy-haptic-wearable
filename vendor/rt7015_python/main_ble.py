import asyncio
import csv
import sys

import datetime
from itertools import zip_longest

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal, QObject, QEventLoop, QTimer, Qt
from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel, QVBoxLayout, QWidget
from PyQt5.QtWidgets import *
from bleak import BleakScanner, BleakClient
from matplotlib import pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
import qasync
from rt7015 import RT7015Data, RT7015Cmd

# 主线程
class MainWindow(QMainWindow, QObject):
    def __init__(self):
        super().__init__()

        # 数据缓存及解析
        self.RtData = RT7015Data()
        # 蓝牙操作对象
        self.client = None
        # 需要连接设备的地址
        self.address = "70:15:88:88:88:88"
        # 接收通知的UUID
        self.notify_uuid = "0000FFF4-0000-1000-8000-00805F9B34FB"
        # 写数据的UUID
        self.write = "0000FFF3-0000-1000-8000-00805F9B34FB"
        # 初始化UI
        self.initUI()

    def initUI(self):

        # 界面的标题
        self.setWindowTitle('RT7015模块-Python示例')

        # 界面的位置和大小的设置
        self.setGeometry(100, 100, 1200, 600)

        # 创建整体界面的水平布局
        hbox = QHBoxLayout()

        # 左侧垂直布局
        left_vbox = QVBoxLayout()
        left_vbox.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        left_vbox.addSpacerItem(QSpacerItem(120, 80, QSizePolicy.Fixed, QSizePolicy.Fixed))
        left_vbox.setSpacing(15)

        # 创建下拉框/信号类型
        self.port_combo = QComboBox(self)
        self.port_combo.addItems([])
        self.btn_connect = QPushButton('连接设备', self)
        self.btn_connect.clicked.connect(self.connect_device)
        self.btn_scan = QPushButton('扫描设备', self)
        self.btn_scan.clicked.connect(self.scan_device)
        self.btn_start = QPushButton('开始采集', self)
        self.btn_start.clicked.connect(self.start_collect)
        self.btn_export_csv = QPushButton('导出数据', self)
        self.btn_export_csv.clicked.connect(self.export_csv)

        left_vbox.addWidget(self.port_combo)
        left_vbox.addWidget(self.btn_scan)
        left_vbox.addWidget(self.btn_connect)
        left_vbox.addWidget(self.btn_start)
        left_vbox.addWidget(self.btn_export_csv)

        # 右侧垂直布局
        right_vbox = QVBoxLayout()

        # # 创建一个绘图区域（QWidget）
        self.drawing_area = QWidget(self)
        self.drawing_area.setFixedSize(10, 10)
        left_vbox.addWidget(self.drawing_area)

        # 创建一个FigureCanvas来显示图像
        self.fig, self.axes = plt.subplots(2, 1, layout='constrained')
        self.canvas = FigureCanvas(self.fig)
        self.toolbar = NavigationToolbar(self.canvas, self)

        right_vbox.addWidget(self.canvas)
        right_vbox.addWidget(self.toolbar)

        hbox.addLayout(left_vbox)
        hbox.addLayout(right_vbox)

        # 创建一个中心小部件并设置布局
        central_widget = QWidget()
        central_widget.setLayout(hbox)
        self.setCentralWidget(central_widget)

        # 设置支持中文的字体
        plt.rcParams['font.sans-serif'] = ['SimHei']  # 使用黑体
        plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题

        # 设置图形和坐标轴
        self.line_a_out, = self.axes[0].plot([], [], lw=1, color='blue', label='A_OUT')
        self.line_s_out, = self.axes[0].plot([], [], lw=1, color='red', label='S_OUT')
        self.axes[0].grid(True)
        self.axes[0].legend(loc="upper right")
        self.axes[0].set_xlim(0, 300)
        self.axes[0].set_ylim(0, 3500)
        self.axes[0].set_ylabel("mV")
        self.axes[0].set_title('PPG模拟信号和PPG方波信号')

        self.line_hr, = self.axes[1].plot([], [], lw=1, color='blue', label='Hr')
        self.axes[1].grid(True)
        self.axes[1].legend(loc="upper right")
        self.axes[1].set_xlim(0, 300)
        self.axes[1].set_ylim(40, 200)
        self.axes[1].set_ylabel("Hr")
        self.axes[1].set_title('心率曲线')

        # 定时读取数据、处理数据、显示数据
        self.update_show_timer = QTimer(self)
        self.update_show_timer.timeout.connect(self.update_show)
        self.update_show_timer.start(100)

    # 扫描设备
    def scan_device(self):
        asyncio.ensure_future(self.ble_scan())

    # 连接设备
    def connect_device(self):
        asyncio.ensure_future(self.ble_connect())

    # 扫描设备
    async def ble_scan(self):
        print("正在扫描...")
        self.btn_scan.setText("正在扫描...")
        # 设备扫描
        devices = await BleakScanner.discover()
        # 将扫描到的设备按照强度从高到低来排
        sorted_devices = sorted(devices, key=lambda device: device.rssi, reverse=True)
        # 打印扫描到的所有设备
        for device in sorted_devices:
            if device.name is not None:
              if device.name.find("RT7015") > -1 :
                print(f"Found device {device.name}: {device.address}: {device.rssi}")
                self.port_combo.addItem(device.address)
        print("扫描完成")
        self.btn_scan.setText("重新扫描")

    # 连接设备
    async def ble_connect(self):
        if self.btn_connect.text() != "断开连接":
            self.btn_connect.setText("正在连接...")
            self.address = self.port_combo.currentText()
            print("Connecting device")
            self.client = BleakClient(self.address)
            await self.client.connect()
            print("Connected to BLE device!")
            await self.client.start_notify(self.notify_uuid, self.data_receive_callback)
            print(f"Subscribed to notifications for {self.notify_uuid}")
            self.btn_connect.setText("断开连接")
        else :
            self.btn_connect.setText("连接设备")
            await self.client.disconnect()
            self.client = None

    async def ble_send_cmd(self,cmd):
        await self.client.write_gatt_char(self.write, bytes(cmd))

    # 数据回调函数
    def data_receive_callback(self, send, received_data):

        # 数据包解析
        self.RtData.parse_data(received_data)

    # 开始采集
    def start_collect(self):
        if self.btn_start.text() == "开始采集":
            self.btn_start.setText("停止采集")
            self.RtData.clear()
            asyncio.ensure_future(self.ble_send_cmd(bytes(RT7015Cmd.start_collect_cmd())))
        else :
            self.btn_start.setText("开始采集")
            asyncio.ensure_future(self.ble_send_cmd(bytes(RT7015Cmd.stop_collect_cmd())))

    # 更新显示
    def update_show(self):

        if self.client  is None:
            return

        # 更新绘图
        if len(self.RtData.a_out_data) > 0:
          self.update_graph(self.axes[0], self.line_a_out, self.RtData.a_out_data, 500)
          self.update_graph(self.axes[0], self.line_s_out, self.RtData.s_out_data, 500)
          self.update_graph(self.axes[1], self.line_hr, self.RtData.s_hr_data, 100)
          self.fig.canvas.draw()
          self.fig.canvas.flush_events()

    # 更新曲线
    def update_graph(self,axes,line,data,rate):

        if len(data) == 0:
            return

        disp_length = rate * 3
        len_n = len(data)
        if len_n < disp_length:
            disp_length = len_n
        x0 = len_n - disp_length
        x1 = len_n
        y_data = data[x0:x1]
        x_data = np.linspace(x0, x1, disp_length)
        line.set_data(x_data,y_data)  # 更新曲线的数据
        axes.set_xlim(x0, x1)

    # 导出csv数据
    def export_csv(self):

        if len(self.RtData.a_out_data)  == 0 :
            QMessageBox.information(None, "提示", "数据缓存区为空，请先采集数据!", QMessageBox.Ok)
            return

        now = datetime.datetime.now()
        default_name = now.strftime("%Y%m%d_%H%M%S") + "_" + self.RtData.signal_type + "_" + str(self.RtData.sample_rate) + "sps"
        filepath, _ = QFileDialog.getSaveFileName(
            None,
            "保存CSV文件",
            default_name,
            "CSV文件 (*.csv);;所有文件 (*)"
        )

        if filepath == "":
           return

        # 表头
        headers = ["OUT1","OUT2","HR"]

        # 写入CSV文件（带表头）
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(headers)  # 先写入表头
            writer.writerows([
                [
                    f"{num1:.3f}" if num1 is not None else "",
                    f"{num2:.3f}" if num2 is not None else "",
                    str(num3) if num3 is not None else ""
                ]
                for num1, num2, num3 in zip_longest(self.RtData.a_out_data, self.RtData.s_out_data, self.RtData.s_hr_data, fillvalue=None)
            ])

        QMessageBox.information(None, "提示", "导出成功！", QMessageBox.Ok)

async def main():
    app = QApplication(sys.argv)
    # 使用 qasync 将 asyncio 事件循环与 PyQt 集成
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    # 窗体UI
    main_window = MainWindow()
    main_window.show()

    # 启动事件循环
    with loop:
        loop.run_forever()

if __name__ == '__main__':
    asyncio.run(main())
