; Bộ cài Windows cho SAM3 Label Assist (Inno Setup 6). Biên dịch trong GitHub Actions:
;   ISCC /DAppVersion=0.6.0 /DBuildDir=..\build installer\sam3-assist.iss
; Cài cho riêng người dùng hiện tại (không cần quyền admin). Phần nặng (PyTorch, trọng số) được tải ở bước cuối.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef BuildDir
  #define BuildDir "..\build"
#endif
#define AppName "SAM3 Label Assist"
#define ExeName "SAM3 Assist.exe"

[Setup]
AppId={{8F3C2E51-6B1A-4C9E-9C47-5A3D2B7E1F10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=11thanh2233
AppPublisherURL=https://github.com/11thanh2233/csam-assist
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=SAM3-Label-Assist-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#ExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Ảnh thương hiệu (dựng bằng scripts/make_icons.py): dải xanh đậm có logo, logo nhỏ ở góc phải
WizardImageFile=wizard.bmp,wizard-2x.bmp
WizardSmallImageFile=wizard-small.bmp,wizard-small-2x.bmp
AppMutex=SAM3CvatAssistDesktopMutex
CloseApplications=yes

[Languages]
; Hai ngôn ngữ: bộ cài hỏi chọn khi mở, app dùng luôn ngôn ngữ đó (tham số --lang).
; Tiếng Anh dùng nguyên văn của Inno Setup (Default.isl), tiếng Việt viết ở [Messages] với tiền tố "vi.".
Name: "vi"; MessagesFile: "compiler:Default.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[LangOptions]
; Tên hiện trong ô chọn ngôn ngữ. Cả hai ngôn ngữ dùng chung Default.isl nên phải đặt riêng, không thì cùng hiện "English".
; LanguageID giúp Windows tiếng Việt tự chọn sẵn Tiếng Việt.
vi.LanguageName=Tiếng Việt
vi.LanguageID=$042A
en.LanguageName=English
en.LanguageID=$0409

[Messages]
vi.SetupAppTitle=Cài đặt
vi.SetupWindowTitle=Cài đặt %1
vi.WelcomeLabel1=Cài đặt [name]
vi.WelcomeLabel2=Bộ cài sẽ chép [name/ver] vào máy.%n%nỞ bước cuối, app hỏi token Hugging Face rồi tải PyTorch và trọng số SAM 3 (khoảng 6 GB). Cần mạng Internet và khoảng 12 GB trống.%n%nNên đóng các ứng dụng khác trước khi tiếp tục.
vi.ButtonNext=&Tiếp >
vi.ButtonBack=< &Quay lại
vi.ButtonCancel=Huỷ
vi.ButtonInstall=&Cài đặt
vi.ButtonFinish=&Xong
vi.ButtonBrowse=&Chọn...
vi.WizardSelectDir=Chọn thư mục cài
vi.SelectDirDesc=Cài [name] vào đâu?
vi.SelectDirLabel3=Code của app được cài vào thư mục dưới đây. Model và dữ liệu nằm riêng ở AppData\Local\SAM3CvatAssist.
vi.SelectDirBrowseLabel=Bấm Tiếp để tiếp tục, hoặc Chọn... để đổi thư mục.
vi.WizardSelectTasks=Tuỳ chọn thêm
vi.SelectTasksDesc=Chọn việc bộ cài làm thêm
vi.SelectTasksLabel2=Chọn rồi bấm Tiếp.
vi.WizardReady=Sẵn sàng cài
vi.ReadyLabel1=Bộ cài đã sẵn sàng chép [name] vào máy.
vi.ReadyLabel2a=Bấm Cài đặt để bắt đầu.
vi.WizardInstalling=Đang cài
vi.InstallingLabel=Đang chép [name] vào máy...
vi.FinishedHeadingLabel=Đã cài xong [name]
vi.FinishedLabel=[name] đã được cài. Mở app từ biểu tượng trên Desktop hoặc trong Start Menu.
vi.ClickFinish=Bấm Xong để đóng bộ cài.
vi.ExitSetupTitle=Thoát bộ cài
vi.ExitSetupMessage=Chưa cài xong. Thoát bây giờ thì app chưa được cài.%n%nThoát?
vi.SetupAppRunningError=[name] đang chạy. Hãy thoát app (chuột phải biểu tượng ở khay hệ thống → Thoát) rồi bấm OK.
vi.UninstallAppRunningError=[name] đang chạy. Hãy thoát app rồi bấm OK.
vi.SelectLanguageTitle=Chọn ngôn ngữ / Select language
vi.SelectLanguageLabel=Chọn ngôn ngữ cho bộ cài và app / Select the language for setup and the app:
en.SelectLanguageTitle=Chọn ngôn ngữ / Select language
en.SelectLanguageLabel=Chọn ngôn ngữ cho bộ cài và app / Select the language for setup and the app:
en.WelcomeLabel2=Setup will copy [name/ver] to your computer.%n%nAt the last step the app asks for your Hugging Face token, then downloads PyTorch and the SAM 3 weights (about 6 GB). An Internet connection and about 12 GB of free space are needed.%n%nClose other applications before continuing.
en.SelectDirLabel3=The app's code is installed into the folder below. Models and data are kept separately in AppData\Local\SAM3CvatAssist.
en.SetupAppRunningError=[name] is running. Quit the app (right-click the tray icon → Quit), then press OK.
en.UninstallAppRunningError=[name] is running. Quit the app, then press OK.

[CustomMessages]
vi.CreateDesktopIcon=Tạo biểu tượng trên Desktop
vi.AdditionalIcons=Biểu tượng:
vi.OpeningDownloader=Đang mở trình tải PyTorch và trọng số SAM 3...
vi.OpenApp=Mở %1
vi.NoVcRedist=Chưa cài được Microsoft Visual C++ Redistributable (x64), thư viện PyTorch cần để chạy (có thể do bạn chọn Không ở hộp xin quyền admin).%n%nApp vẫn được cài. Khi mở app, trang Tổng quan có nút tải gói này; hoặc tải tại:%nhttps://aka.ms/vs/17/release/vc_redist.x64.exe
vi.InstallingVcRedist=Đang cài Microsoft Visual C++ Redistributable (Windows sẽ xin quyền admin)...
vi.NoWebView2=Máy chưa có Microsoft Edge WebView2 Runtime (Windows 11 thường có sẵn). App cần nó để mở CVAT.%n%nSau khi cài xong, nếu app báo lỗi WebView2, hãy tải bản "Evergreen Bootstrapper" tại:%nhttps://developer.microsoft.com/microsoft-edge/webview2/
vi.DeleteModels=Xoá cả PyTorch và trọng số model đã tải (khoảng 9 GB)?%nGiữ lại thì lần cài sau không phải tải lại.
vi.DeleteData=Xoá cả dữ liệu của bạn: profile, guideline, dữ liệu tự học, cài đặt và đăng nhập CVAT trong app?%nChọn Không nếu định cài lại sau.
vi.InstallingWebView2=Đang cài Microsoft Edge WebView2 Runtime (app cần để mở cửa sổ và CVAT)...
vi.TaskWebView2=Cài Microsoft Edge WebView2 Runtime (máy chưa có, app cần nó)
vi.TaskWsl=Cài WSL2 để chạy CVAT trên chính máy này (Windows xin quyền admin, cần khởi động lại máy). Bỏ qua nếu dùng CVAT trên web
vi.ExtraGroup=Phần mềm phụ:
vi.WslStarted=Đã chạy lệnh cài WSL2. Khởi động lại máy khi Windows báo xong. Docker Desktop cài riêng (app có nút mở trang tải ở trang Hỗ trợ).
en.InstallingWebView2=Installing Microsoft Edge WebView2 Runtime (the app needs it for its window and CVAT)...
en.TaskWebView2=Install Microsoft Edge WebView2 Runtime (missing, the app needs it)
en.TaskWsl=Install WSL2 to run CVAT on this computer (Windows asks for admin rights, restart needed). Skip if you use CVAT on the web
en.ExtraGroup=Extra software:
en.WslStarted=The WSL2 install command was started. Restart the computer when Windows says it is done. Install Docker Desktop separately (the app's Support page has a button for its download page).
en.CreateDesktopIcon=Create a desktop icon
en.AdditionalIcons=Icons:
en.OpeningDownloader=Opening the PyTorch and SAM 3 weights downloader...
en.OpenApp=Open %1
en.NoVcRedist=Microsoft Visual C++ Redistributable (x64), which PyTorch needs, could not be installed (perhaps No was chosen in the admin prompt).%n%nThe app is still installed. Its Overview page has a button to download the package, or get it at:%nhttps://aka.ms/vs/17/release/vc_redist.x64.exe
en.InstallingVcRedist=Installing Microsoft Visual C++ Redistributable (Windows will ask for admin rights)...
en.NoWebView2=Microsoft Edge WebView2 Runtime is not installed (Windows 11 usually has it). The app needs it to open CVAT.%n%nAfter installing, if the app reports a WebView2 error, download the "Evergreen Bootstrapper" at:%nhttps://developer.microsoft.com/microsoft-edge/webview2/
en.DeleteModels=Also delete the downloaded PyTorch and model weights (about 9 GB)?%nKeep them so the next install does not download them again.
en.DeleteData=Also delete your data: profiles, guidelines, learning data, settings and the CVAT login in the app?%nChoose No if you plan to reinstall later.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
; 0.9.4: phần mềm phụ. Chỉ hiện khi máy thiếu; WebView2 chọn sẵn, WSL2 để bạn tự chọn.
Name: "webview2"; Description: "{cm:TaskWebView2}"; GroupDescription: "{cm:ExtraGroup}"; Check: NeedWebView2
Name: "wsl"; Description: "{cm:TaskWsl}"; GroupDescription: "{cm:ExtraGroup}"; Flags: unchecked; Check: CanOfferWsl

[InstallDelete]
; Cập nhật: xoá code và Python cũ để không còn file thừa. Dữ liệu của bạn (model, profile, tự học) nằm chỗ khác, không bị đụng tới.
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\runtime"
; 0.9.1 đổi tên SAM3 CVAT Assist -> SAM3 Label Assist: xoá lối tắt tên cũ để không có hai biểu tượng
Type: files; Name: "{userprograms}\SAM3 CVAT Assist.lnk"
Type: files; Name: "{userdesktop}\SAM3 CVAT Assist.lnk"

[Files]
Source: "{#BuildDir}\runtime\*"; DestDir: "{app}\runtime"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#BuildDir}\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#BuildDir}\{#ExeName}"; DestDir: "{app}"; Flags: ignoreversion
; Gói cài chính thức của Microsoft (CI tải và kiểm chữ ký số). Chỉ giải nén khi máy thiếu, xem CurStepChanged.
Source: "{#BuildDir}\vc_redist.x64.exe"; Flags: dontcopy
; 0.9.4: bộ cài nhỏ chính thức của WebView2 (Evergreen Bootstrapper, CI kiểm chữ ký). Chỉ chạy khi máy thiếu.
Source: "{#BuildDir}\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy

[Icons]
; AppUserModelID trùng với app (desktop/app.py) để taskbar gom đúng một biểu tượng SAM3 Assist
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#ExeName}"; AppUserModelID: "SAM3CvatAssist.App"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#ExeName}"; Tasks: desktopicon; AppUserModelID: "SAM3CvatAssist.App"

[Run]
Filename: "{app}\{#ExeName}"; Parameters: "--setup --lang={language}"; StatusMsg: "{cm:OpeningDownloader}"; Flags: waituntilterminated skipifsilent
Filename: "{app}\{#ExeName}"; Description: "{cm:OpenApp,{#AppName}}"; Flags: postinstall nowait skipifsilent
; Cập nhật từ trong app (bộ cài chạy /SILENT /update=1): xong thì mở lại app.
Filename: "{app}\{#ExeName}"; Flags: nowait; Check: IsUpdate

[UninstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\runtime"
; bản cũ script cập nhật giữ lại để quay về (desktop/safe_update.py)
Type: filesandordirs; Name: "{app}\app.prev"
Type: filesandordirs; Name: "{app}\runtime.prev"
Type: files; Name: "{app}\launcher.prev"

[Code]
const
  WebView2Key = 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function HasWebView2(): Boolean;
var v: String;
begin
  Result := (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) and (v <> '') and (v <> '0.0.0.0'))
    or (RegQueryStringValue(HKLM, WebView2Key, 'pv', v) and (v <> '') and (v <> '0.0.0.0'))
    or (RegQueryStringValue(HKCU, WebView2Key, 'pv', v) and (v <> '') and (v <> '0.0.0.0'));
end;

function IsUpdate(): Boolean;
begin
  Result := ExpandConstant('{param:update|0}') = '1';
end;

function HasVcRedist(): Boolean;
var installed: Cardinal;
begin
  Result := (RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', installed) and (installed = 1))
    or (RegQueryDWordValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', installed) and (installed = 1));
end;

function InitializeSetup(): Boolean;
begin
  Result := True;                             // cập nhật từ trong app: máy đã chạy được bản trước
end;

// Task checks: offer WebView2 only when missing, WSL2 only on Windows 10 2004+ without wsl.exe.
function NeedWebView2(): Boolean;
begin
  Result := (not IsUpdate()) and (not HasWebView2());
end;

function CanOfferWsl(): Boolean;
var v: TWindowsVersion;
begin
  GetWindowsVersionEx(v);
  Result := (not IsUpdate()) and (v.Build >= 19041) and (not FileExists(ExpandConstant('{sys}\wsl.exe')));
end;

// WebView2 missing: run Microsoft's bootstrapper silently (per-user without admin, per-machine when elevated).
// It needs the Internet; if it fails the user gets the old message with the download page.
procedure InstallWebView2();
var rc: Integer; exe: String;
begin
  if HasWebView2() then
    exit;
  WizardForm.StatusLabel.Caption := CustomMessage('InstallingWebView2');
  ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
  exe := ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe');
  if not Exec(exe, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, rc) then
    rc := -1;
  Log(Format('webview2 bootstrapper exit code %d', [rc]));
  if not HasWebView2() then
    SuppressibleMsgBox(CustomMessage('NoWebView2'), mbInformation, MB_OK, IDOK);
end;

// Optional WSL2 (only for running CVAT locally). Needs admin and a restart; Docker Desktop is left to the user.
procedure InstallWsl();
var rc: Integer;
begin
  if ShellExec('runas', ExpandConstant('{sys}\wsl.exe'), '--install --no-distribution', '', SW_SHOW, ewWaitUntilTerminated, rc) then
    SuppressibleMsgBox(CustomMessage('WslStarted'), mbInformation, MB_OK, IDOK);
  Log(Format('wsl --install exit code %d', [rc]));
end;

// Microsoft Visual C++ (x64) missing: install it before copying files. It installs machine-wide, so Windows asks for admin
// rights; if the user says No the app is still installed and explains how to install it later (the app also detects this).
procedure InstallVcRedist();
var rc: Integer; exe: String;
begin
  if HasVcRedist() then
    exit;
  WizardForm.StatusLabel.Caption := CustomMessage('InstallingVcRedist');
  ExtractTemporaryFile('vc_redist.x64.exe');
  exe := ExpandConstant('{tmp}\vc_redist.x64.exe');
  if not ShellExec('runas', exe, '/install /quiet /norestart', '', SW_SHOW, ewWaitUntilTerminated, rc) then
    rc := -1;
  Log(Format('vc_redist exit code %d', [rc]));     // 0 done, 3010 done but needs reboot, 1638 newer version present
  if not HasVcRedist() then
    SuppressibleMsgBox(CustomMessage('NoVcRedist'), mbInformation, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssInstall) and not IsUpdate() then
  begin
    InstallVcRedist();
    if WizardIsTaskSelected('webview2') then
      InstallWebView2();
  end;
  if (CurStep = ssPostInstall) and (not IsUpdate()) and WizardIsTaskSelected('wsl') then
    InstallWsl();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var data: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'SAM3 Label Assist');
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'SAM3 CVAT Assist');   { tên trước 0.9.1 }
    data := ExpandConstant('{localappdata}\SAM3CvatAssist');
    if DirExists(data) then
    begin
      if SuppressibleMsgBox(CustomMessage('DeleteModels'), mbConfirmation, MB_YESNO, IDNO) = IDYES then
      begin
        DelTree(data + '\packages', True, True, True);
        DelTree(data + '\models', True, True, True);
        DelTree(data + '\downloads', True, True, True);
      end;
      if SuppressibleMsgBox(CustomMessage('DeleteData'), mbConfirmation, MB_YESNO, IDNO) = IDYES then
        DelTree(data, True, True, True);
    end;
  end;
end;
