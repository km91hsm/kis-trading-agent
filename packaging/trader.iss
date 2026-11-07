; Trader Windows 설치 프로그램 (Inno Setup 6). packaging/build.py 의 make_setup() 이 컴파일한다:
;   ISCC.exe /DMyVersion=<버전> /DBundleDir=<dist\trader> /O<dist> /F<이름>-setup packaging\trader.iss
; 사용자 단위 설치(관리자 권한 불필요): 앱은 %LOCALAPPDATA%\Programs\Trader, 데이터는
; %LOCALAPPDATA%\Trader(앱이 첫 실행 때 만든다). 설치·업데이트·제거 어느 쪽도 데이터 폴더를 지우지 않는다.
; 한글 주석 때문에 이 파일은 UTF-8(BOM) 으로 둔다(tests/packaging/test_build.py 가 확인).

#ifndef MyVersion
  #define MyVersion "0.0.0"
#endif
#ifndef BundleDir
  #define BundleDir "..\dist\trader"
#endif

[Setup]
; AppId 는 바꾸지 않는다 — 바뀌면 업데이트가 같은 앱으로 인식되지 않는다
AppId={{F0FDFBA7-B430-40FC-B1F0-0063C95C0432}
AppName=Trader
AppVersion={#MyVersion}
AppVerName=Trader {#MyVersion}
AppPublisher=kis-trading-agent
AppPublisherURL=https://github.com/km91hsm/kis-trading-agent
VersionInfoVersion={#MyVersion}
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Trader
DisableProgramGroupPage=yes
DisableDirPage=auto
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=icons\trader.ico
UninstallDisplayIcon={app}\TraderApp.exe
UninstallDisplayName=Trader
; 실행 중인 트레이 앱·데몬·Chrome Native host(trader.exe)가 앱 파일을 잡고 있으면 닫기를 묻는다
CloseApplications=yes
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
OutputDir=..\dist
OutputBaseFilename=trader-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
; 앱 파일만 담는다. dist\trader 최상위에 사용자 데이터가 있어도 넣지 않는다(맨 앞 \ = 최상위만 —
; _internal\templates\secrets 같은 번들 리소스는 그대로 들어간다)
Source: "{#BundleDir}\*"; DestDir: "{app}"; Excludes: "\config\,\secrets\,\state\,\docs\,\README.md"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; 업데이트 때 이전 버전 런타임이 섞이지 않게 앱 폴더의 _internal 만 비우고 새로 깐다(데이터 폴더와 무관)
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{userprograms}\Trader"; Filename: "{app}\TraderApp.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\TraderApp.exe"; Description: "Trader 실행"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; 제거 때만 돈다(업데이트 설치에서는 돌지 않는다). 로그온 자동 시작 작업과 Chrome Native host 등록만
; 해제한다 — 데이터 폴더(%LOCALAPPDATA%\Trader)의 설정·키·원장·수집 파일은 지우지 않는다.
; 종료 코드는 보지 않으므로 실패(등록돼 있지 않음·확장이 연결 중)는 제거를 막지 않는다.
Filename: "{app}\trader.exe"; Parameters: "install --uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "TraderUnregisterLogon"
Filename: "{app}\trader.exe"; Parameters: "install --uninstall --watchdog"; Flags: runhidden waituntilterminated; RunOnceId: "TraderUnregisterWatchdog"
Filename: "{app}\trader.exe"; Parameters: "saveticker extension uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "TraderUnregisterSaveticker"
