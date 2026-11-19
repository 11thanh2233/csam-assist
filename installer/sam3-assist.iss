; Bộ cài Windows cho SAM3 CVAT Assist (Inno Setup 6). Biên dịch trong GitHub Actions:
;   ISCC /DAppVersion=0.6.0 /DBuildDir=..\build installer\sam3-assist.iss
; Cài cho riêng người dùng hiện tại (không cần quyền admin). Phần nặng (PyTorch, trọng số) được tải ở bước cuối.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef BuildDir
  #define BuildDir "..\build"
#endif
#define AppName "SAM3 CVAT Assist"
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
OutputBaseFilename=SAM3-CVAT-Assist-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#ExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
AppMutex=SAM3CvatAssistDesktopMutex
CloseApplications=yes

[Languages]
Name: "vi"; MessagesFile: "compiler:Default.isl"

[Messages]
SetupAppTitle=Cài đặt
SetupWindowTitle=Cài đặt %1
WelcomeLabel1=Cài đặt [name]
WelcomeLabel2=Bộ cài sẽ chép [name/ver] vào máy.%n%nỞ bước cuối, app hỏi token Hugging Face rồi tải PyTorch và trọng số SAM 3 (khoảng 6 GB). Cần mạng Internet và khoảng 12 GB trống.%n%nNên đóng các ứng dụng khác trước khi tiếp tục.
ButtonNext=&Tiếp >
ButtonBack=< &Quay lại
ButtonCancel=Huỷ
ButtonInstall=&Cài đặt
ButtonFinish=&Xong
ButtonBrowse=&Chọn...
WizardSelectDir=Chọn thư mục cài
SelectDirDesc=Cài [name] vào đâu?
SelectDirLabel3=Code của app được cài vào thư mục dưới đây. Model và dữ liệu nằm riêng ở AppData\Local\SAM3CvatAssist.
SelectDirBrowseLabel=Bấm Tiếp để tiếp tục, hoặc Chọn... để đổi thư mục.
WizardSelectTasks=Tuỳ chọn thêm
SelectTasksDesc=Chọn việc bộ cài làm thêm
SelectTasksLabel2=Chọn rồi bấm Tiếp.
WizardReady=Sẵn sàng cài
ReadyLabel1=Bộ cài đã sẵn sàng chép [name] vào máy.
ReadyLabel2a=Bấm Cài đặt để bắt đầu.
WizardInstalling=Đang cài
InstallingLabel=Đang chép [name] vào máy...
FinishedHeadingLabel=Đã cài xong [name]
FinishedLabel=[name] đã được cài. Mở app từ biểu tượng trên Desktop hoặc trong Start Menu.
ClickFinish=Bấm Xong để đóng bộ cài.
ExitSetupTitle=Thoát bộ cài
ExitSetupMessage=Chưa cài xong. Thoát bây giờ thì app chưa được cài.%n%nThoát?
SetupAppRunningError=[name] đang chạy. Hãy thoát app (chuột phải biểu tượng ở khay hệ thống → Thoát) rồi bấm OK.
UninstallAppRunningError=[name] đang chạy. Hãy thoát app rồi bấm OK.

[CustomMessages]
vi.CreateDesktopIcon=Tạo biểu tượng trên Desktop
vi.AdditionalIcons=Biểu tượng:

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Cập nhật: xoá code và Python cũ để không còn file thừa. Dữ liệu của bạn (model, profile, tự học) nằm chỗ khác, không bị đụng tới.
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\runtime"

[Files]
Source: "{#BuildDir}\runtime\*"; DestDir: "{app}\runtime"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#BuildDir}\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#BuildDir}\{#ExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#ExeName}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#ExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#ExeName}"; Parameters: "--setup"; StatusMsg: "Đang mở trình tải PyTorch và trọng số SAM 3..."; Flags: waituntilterminated skipifsilent
Filename: "{app}\{#ExeName}"; Description: "Mở {#AppName}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\runtime"

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

function HasVcRedist(): Boolean;
var installed: Cardinal;
begin
  Result := (RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', installed) and (installed = 1))
    or (RegQueryDWordValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', installed) and (installed = 1));
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not HasVcRedist() then
    SuppressibleMsgBox('Máy chưa có Microsoft Visual C++ Redistributable (x64). PyTorch cần nó để chạy.' + #13#10#13#10 +
      'Hãy tải và cài (cần quyền admin) từ trang chính thức của Microsoft:' + #13#10 +
      'https://aka.ms/vs/17/release/vc_redist.x64.exe' + #13#10#13#10 + 'Có thể cài trước hoặc sau khi cài app.',
      mbInformation, MB_OK, IDOK);
  if not HasWebView2() then
    SuppressibleMsgBox('Máy chưa có Microsoft Edge WebView2 Runtime (Windows 11 thường có sẵn). App cần nó để mở CVAT.' + #13#10#13#10 +
      'Sau khi cài xong, nếu app báo lỗi WebView2, hãy tải bản "Evergreen Bootstrapper" tại:' + #13#10 +
      'https://developer.microsoft.com/microsoft-edge/webview2/', mbInformation, MB_OK, IDOK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var data: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'SAM3 CVAT Assist');
    data := ExpandConstant('{localappdata}\SAM3CvatAssist');
    if DirExists(data) then
    begin
      if SuppressibleMsgBox('Xoá cả PyTorch và trọng số model đã tải (khoảng 9 GB)?' + #13#10 + 'Giữ lại thì lần cài sau không phải tải lại.',
                            mbConfirmation, MB_YESNO, IDNO) = IDYES then
      begin
        DelTree(data + '\packages', True, True, True);
        DelTree(data + '\models', True, True, True);
        DelTree(data + '\downloads', True, True, True);
      end;
      if SuppressibleMsgBox('Xoá cả dữ liệu của bạn: profile, guideline, dữ liệu tự học, cài đặt và đăng nhập CVAT trong app?' + #13#10 +
                            'Chọn Không nếu định cài lại sau.', mbConfirmation, MB_YESNO, IDNO) = IDYES then
        DelTree(data, True, True, True);
    end;
  end;
end;
