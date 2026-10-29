import asyncio
import csv
import sys

import datetime
from itertools import zip_longest

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal, QObject, QEventLoop, QTimer, Qt
from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel, QVBoxLayout, QWidget
from PyQt5.QtWidgets import *
from matplotlib import pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
import qasync
import serial
from serial.tools import list_ports
from rt7015 import RT7015Data, RT7015Cmd

# 主线程
class MainWindow(QMainWindow, QObject):
    def __init__(self):
        super().__init__()
        self.initUI()
        self.RtData = RT7015Data() # 数据缓存及解析
        self.serial_port = None # 串口对象

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
        hbox1 = QHBoxLayout()
        hbox1.addWidget(QLabel("选择串口"))
        hbox1.addWidget(self.port_combo)

        self.btn_open = QPushButton('打开串口', self)
        self.btn_open.clicked.connect(self.open_serial)
        self.btn_start = QPushButton('开始采集', self)
        self.btn_start.clicked.connect(self.start_collect)
        self.btn_export_csv = QPushButton('导出数据', self)
        self.btn_export_csv.clicked.connect(self.export_csv)

        left_vbox.addLayout(hbox1)
        left_vbox.addWidget(self.btn_open)
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

        # 更新串口列表
        self.refresh_ports()

    #  刷新可用的串口列表
    def refresh_ports(self):
        # 保存当前选择的串口
        current_selection = self.port_combo.currentText()
        self.port_combo.clear()

        # 获取所有可用串口
        ports = serial.tools.list_ports.comports()

        if not ports:
            self.port_combo.addItem("未检测到串口")
            self.port_combo.setEnabled(False)
            self.btn_open.setEnabled(False)
        else:
            for port in sorted(ports):
                self.port_combo.addItem(port.device, port.description)
            self.port_combo.setEnabled(True)
            self.btn_open.setEnabled(True)

            # 尝试恢复之前的选择
            if current_selection in [self.port_combo.itemText(i) for i in range(self.port_combo.count())]:
                self.port_combo.setCurrentText(current_selection)
            elif ports:  # 默认选择第一个串口
                self.port_combo.setCurrentIndex(0)

    # 打开或关闭串口
    def open_serial(self):
       if self.btn_open.text() == "打开串口":
            port_name = self.port_combo.currentText()
            # 打开串口，将波特率配置为256000，数据位为8，停止位为1，无校验位，读超时时间为0.5秒。
            self.serial_port = serial.Serial(port=port_name,
                                             baudrate=256000,
                                             bytesize=serial.EIGHTBITS,
                                             parity=serial.PARITY_NONE,
                                             stopbits=serial.STOPBITS_ONE,
                                             timeout=0.5)

            if self.serial_port.isOpen():  # 判断串口是否成功打开
                self.btn_open.setText("关闭串口")
                print("打开串口成功。")
                print(self.serial_port.name)  # 输出串口号
            else:
                self.btn_open.setText("打开串口")
                self.serial_port = None
                print("打开串口失败。")
       else:
           self.btn_open.setText("打开串口")
           self.serial_port.close()
           self.serial_port = None

    # 开始采集
    def start_collect(self):
        if self.btn_start.text() == "开始采集":
            self.btn_start.setText("停止采集")
            self.RtData.clear()
            self.serial_port.write(bytes(RT7015Cmd.start_collect_cmd()))
        else :
            self.btn_start.setText("开始采集")
            self.serial_port.write(bytes(RT7015Cmd.stop_collect_cmd()))

    # 更新显示
    def update_show(self):

        if  self.serial_port is None:
            return

        # 获取串口已接收的数据
        n = self.serial_port.in_waiting
        if n > 0:
           # 读取串口数据
           com_data =  self.serial_port.read(n)
           # 解析数据
           self.RtData.parse_data(com_data)
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
