# Handwrite

把 Word / PDF / 文本作业拖进来，变成像真人用圆珠笔写在纸上的 PDF。
全程在你自己的电脑上完成，不联网、不上传。

Drop a Word, PDF or text file; get pages that look hand-written with a ballpoint pen.
Runs entirely offline. Windows and macOS.

## 使用

1. 双击 `Handwrite.exe`（Windows）或 `Handwrite.app`（Mac）。
2. 把文件拖进窗口，或点「打开文件」。
3. 右侧用 ‹ › 调纸张、纸质、字迹、笔色、潦草程度、字号、倾斜、字距、涂改痕迹、扫描感。
4. 「换一种笔迹」生成一套新的书写习惯（每个字母固定的倾斜和大小），「重新排一版」换一种排布。
5. 「导出 PDF」。要打印就把「扫描感」关掉。

切到「文字」可以修改要写的内容。格式：`# ` 开头是标题，空行空一行，行首空格缩进，
`x^2` 上标，`H_{2}O` 下标，`\frac{1}{n}` 叠式分数，`\sum_{i=1}^{n}` 带上下限的 Σ，`\hat{x}` `\bar{x}` 帽子和横线。

首次打开时 Windows 会提示「Windows 已保护你的电脑」，点「更多信息 → 仍要运行」；
Mac 要右键 → 打开。这是因为安装包没有花钱签名，不是病毒。

## 从源码运行

```bash
pip install -r requirements.txt
python app/main.py          # 原生窗口
python app/main.py --web    # 在浏览器里打开 http://127.0.0.1:8791
python build.py             # 打包成 exe / app
```

## 为什么看起来像真人写的

每个字母单独画：各自的倾斜、大小、笔画弯曲、墨色轻重，再叠一套这个"人"固定的书写习惯；
字母之间偶尔连笔，长单词越写越懒，行尾挤字而不是换行，偶尔写错划掉重写。
字体是 6 款 SIL OFL 开源手写体（`app/fonts/`）。

## 许可

代码 MIT，字体各按其 OFL 许可证。
