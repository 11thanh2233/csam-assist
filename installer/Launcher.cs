// "SAM3 Assist.exe": chạy app bằng Python bản nhúng nằm cạnh file exe, không mở cửa sổ console.
// Biên dịch: csc /target:winexe /win32icon:icon.ico /out:"SAM3 Assist.exe" Launcher.cs
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Windows.Forms;

static class Launcher
{
    static readonly string[] Allowed = { "--setup", "--tray" };

    [STAThread]
    static int Main(string[] args)
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string py = Path.Combine(dir, "runtime", "pythonw.exe");
        if (!File.Exists(py))
        {
            MessageBox.Show("Không thấy Python của app (" + py + "). Hãy cài lại SAM3 CVAT Assist.", "SAM3 CVAT Assist",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        var flags = args.Where(a => Allowed.Contains(a)).ToArray();     // chỉ nhận các tuỳ chọn biết trước
        var psi = new ProcessStartInfo(py, "-I -X utf8 -m desktop " + string.Join(" ", flags))
        {
            WorkingDirectory = Path.Combine(dir, "app"),
            UseShellExecute = false,
        };
        psi.EnvironmentVariables["SAM3_LAUNCHER"] = Application.ExecutablePath;
        var p = Process.Start(psi);
        if (flags.Contains("--setup"))
        {
            p.WaitForExit();                       // bộ cài đợi trình tải thành phần xong
            return p.ExitCode;
        }
        return 0;
    }
}
