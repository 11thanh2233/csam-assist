// "SAM3 Assist.exe": chạy app bằng Python bản nhúng nằm cạnh file exe, không mở cửa sổ console.
// Trong lúc Python khởi động (vài giây), hiện màn hình chờ có logo; app báo "đã sẵn sàng" qua một event của Windows
// (Local\SAM3Assist-Ready) thì màn hình chờ tự đóng.
// Biên dịch: csc /target:winexe /win32icon:icon.ico /r:System.Windows.Forms.dll /r:System.Drawing.dll /out:"SAM3 Assist.exe" Launcher.cs
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Threading;
using System.Windows.Forms;

static class Launcher
{
    static readonly string[] Allowed = { "--setup", "--tray", "--lang=vi", "--lang=en" };
    const string ReadyEvent = @"Local\SAM3Assist-Ready";

    [STAThread]
    static int Main(string[] args)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string py = Path.Combine(dir, "runtime", "pythonw.exe");
        if (!File.Exists(py))
        {
            MessageBox.Show("Không thấy Python của app (" + py + "). Hãy cài lại SAM3 Label Assist.", "SAM3 Label Assist",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        var flags = args.Where(a => Allowed.Contains(a)).ToArray();     // chỉ nhận các tuỳ chọn biết trước
        var ready = new EventWaitHandle(false, EventResetMode.ManualReset, ReadyEvent);
        var psi = new ProcessStartInfo(py, "-I -X utf8 -m desktop " + string.Join(" ", flags))
        {
            WorkingDirectory = Path.Combine(dir, "app"),
            UseShellExecute = false,
        };
        psi.EnvironmentVariables["SAM3_LAUNCHER"] = Application.ExecutablePath;
        var p = Process.Start(psi);
        if (!flags.Contains("--tray"))
            ShowSplash(dir, ready, p, flags.Contains("--lang=en") || EnglishSaved());
        if (flags.Contains("--setup"))
        {
            p.WaitForExit();                       // bộ cài đợi trình tải thành phần xong
            return p.ExitCode;
        }
        return 0;
    }

    // Ngôn ngữ đã chọn trong app (settings.json); không đọc được thì tiếng Việt
    static bool EnglishSaved()
    {
        try
        {
            string f = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                                    "SAM3CvatAssist", "settings.json");
            return File.Exists(f) && File.ReadAllText(f).Replace(" ", "").Contains("\"lang\":\"en\"");
        }
        catch (Exception) { return false; }
    }

    // Màn hình chờ: nền xanh đậm, logo, tên app, dòng "Đang khởi động…", thanh chạy. Đóng khi app sẵn sàng,
    // khi Python thoát, hoặc sau 60 giây.
    static void ShowSplash(string dir, EventWaitHandle ready, Process p, bool english)
    {
        try
        {
            Application.EnableVisualStyles();
            var navy = Color.FromArgb(14, 26, 54);
            var form = new Form
            {
                FormBorderStyle = FormBorderStyle.None, StartPosition = FormStartPosition.CenterScreen,
                Size = new Size(480, 300), BackColor = navy, ShowInTaskbar = false, TopMost = true,
                Text = "SAM3 Label Assist",
            };
            string png = Path.Combine(dir, "app", "desktop", "ui", "icon.png");
            if (File.Exists(png))
                form.Controls.Add(new PictureBox
                {
                    Image = Image.FromFile(png), SizeMode = PictureBoxSizeMode.Zoom,
                    Size = new Size(72, 72), Location = new Point(204, 62), BackColor = navy,
                });
            form.Controls.Add(new Label
            {
                Text = "SAM3 Label Assist", ForeColor = Color.White, Font = new Font("Segoe UI Semibold", 15f),
                TextAlign = ContentAlignment.MiddleCenter, Size = new Size(480, 36), Location = new Point(0, 146),
            });
            form.Controls.Add(new Label
            {
                Text = english ? "Starting…" : "Đang khởi động…", ForeColor = Color.FromArgb(169, 181, 207),
                Font = new Font("Segoe UI", 10f), TextAlign = ContentAlignment.MiddleCenter,
                Size = new Size(480, 22), Location = new Point(0, 182),
            });
            var track = new Panel { BackColor = Color.FromArgb(36, 52, 92), Size = new Size(180, 4), Location = new Point(150, 222) };
            var runner = new Panel { BackColor = Color.FromArgb(111, 155, 255), Size = new Size(60, 4), Location = new Point(-60, 0) };
            track.Controls.Add(runner);
            form.Controls.Add(track);
            var started = DateTime.Now;
            var timer = new System.Windows.Forms.Timer { Interval = 30 };
            timer.Tick += (s, e) =>
            {
                runner.Left = runner.Left > 180 ? -60 : runner.Left + 4;
                if (ready.WaitOne(0) || p.HasExited || (DateTime.Now - started).TotalSeconds > 60)
                {
                    timer.Stop();
                    form.Close();
                }
            };
            form.Shown += (s, e) => timer.Start();
            Application.Run(form);
        }
        catch (Exception)
        {
            // màn hình chờ chỉ để đẹp: lỗi gì cũng bỏ qua, app vẫn chạy
        }
    }
}
