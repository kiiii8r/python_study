import PySimpleGUI as sg

# sg.popup('Hello PysimpleGUI!', '最も簡単なGUIプログラム')

layout = [
    [sg.Text('Hello PySimpleGUI!はじめてのGUIプログラム')],
    [sg.Button('Click')]
]

window = sg.Window('GUI Button', layout)

while True:
    event, value = window.read() # イベント入力を待つ
    if event == 'Click':
        sg.popup('ボタンが押されました')
    if event is None:
        break
window.close()