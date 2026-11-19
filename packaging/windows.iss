; Compile via scripts/build_installer.py; PayloadDir is an audited temporary folder.
[Setup]
AppId={{C1482261-E718-491B-9A42-9C8733A15B31}
AppName=材知
AppVersion={#AppVersion}
AppVerName=材知 {#AppVersion}
DefaultDirName={localappdata}\Programs\MatKnow
DefaultGroupName=材知
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=MatKnow-{#AppVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\MatKnow.exe
CloseApplications=no
RestartApplications=no

[Languages]
Name: "default"; MessagesFile: "{#LanguageFile}"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#PayloadDir}\MatKnow.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\agent.md"; DestDir: "{app}"; Flags: ignoreversion; Check: NeedsPolicy('agent.md')
Source: "{#PayloadDir}\library_agent.md"; DestDir: "{app}"; Flags: ignoreversion; Check: NeedsPolicy('library_agent.md')
Source: "{#PayloadDir}\experiment_agent.md"; DestDir: "{app}"; Flags: ignoreversion; Check: NeedsPolicy('experiment_agent.md')
Source: "{#PayloadDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\材知"; Filename: "{app}\MatKnow.exe"
Name: "{autodesktop}\材知"; Filename: "{app}\MatKnow.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MatKnow.exe"; Description: "启动材知"; Flags: nowait postinstall skipifsilent

[Code]
function NeedsPolicy(PolicyName: String): Boolean;
var
  Filename, Digest: String;
begin
  Filename := ExpandConstant('{app}\') + PolicyName;
  Result := not FileExists(Filename);
  if Result then exit;
  try
    Digest := Lowercase(GetSHA256OfFile(Filename));
    Result := False;
    if PolicyName = 'agent.md' then
      Result := (Digest = '6bf3fe847e8d8fdc9d9ad659586f75f485c58de7de88265f1af66c8543cf6b7a') or
                (Digest = '46b1a74c632fc7dea302b11ee442bc71edcea70f3d44cae4e1c58bf817a91258') or
                (Digest = '8df720b1875f053a82775dd10554f37ffee6538626761bc2f1ebaf1369b6f911') or
                (Digest = '099ac1cf6251d40c5276244ab3558d05bb26c689521ecb5c80bfe58af4341c72') or
                (Digest = '346c87194ee3d85d299d8a1aa1d2de411440e8d69505f3388fcc68619e6bb62d') or
                (Digest = 'a0a81341c23fcb39d22760e5dca99fc5704fd6e3797807724e343c99d23d4895') or
                (Digest = '05a950bc8bc3acf018c5a1ad1b6f8f1fed56ab2e7d64dbf2bcfcb041e1cf5159') or
                (Digest = '1c5c2f41ab33481dbc7138598d0295e75dba374a3cf1e882fd345da5ae0d39a3') or
                (Digest = '34479b46f01974036df4a7c3954c3c94ee7a72f316c3841d407792d7863de68f') or
                (Digest = '9b3e2a0de0e86a39f2d117b149abb01597a207be47b1b9bed153d5a3f8a9818e') or
                (Digest = '512c07aa57c2ce91c17c396202b762590339ea05d23d30819cbfab167f7f4fd4') or
                (Digest = 'e03fe9fc41cb1ef044e46bb95cb165bf82d06f1cf5bfb023a309df6d9aa8bfc3');
    if PolicyName = 'library_agent.md' then
      Result := (Digest = '38f5f3976d8c147f28daaa049b40225dd8fbb77cd27b83e67d90b3aafce2e91d') or
                (Digest = '95be4dcb69600bf7fab4072a76c672b238986551e051437ebcef512e66fbd9c8') or
                (Digest = '97ccdf2923a7626a9bfe1c9638cae7b9b6f045a72106352bfaa565c267c86cb0') or
                (Digest = 'ec53cf3647a7207db870a65723480ac89da0a4fad5a855c642d9712a2aa2ed72') or
                (Digest = 'e715906f33988597fb878a9153c2e8628fb1e0d1a8db18863d3e9a488c32fb78') or
                (Digest = 'baecf1d773801e3be89378f804754a8db830bfe1751650666f18b6964c64c8fa') or
                (Digest = '9855c018f8ac766244b236f061b83be3c53aea4ef35ae2d3b2ad00b42318ba15') or
                (Digest = 'c1576708a9b9fe8b47d5b056918a63cdd8e1381633aba740aa1707b92b13b06f');
    if PolicyName = 'experiment_agent.md' then
      Result := (Digest = '4e503b853050b81c3574835eebb202f093362a8e0f28ec18b1ac769801788323') or
                (Digest = '870d480f16362c1de16e854056ce9adb976aebc91306e567c135ebe75d7c9de5') or
                (Digest = '2cbfdc7015e64d39dc1114471e21c926518447ec973f473d958a57352a5249ef') or
                (Digest = 'bc7a74dd4710453fbced7f3a0a2b0481d2c97bd3a3315a0a6cd3b54fa41e2d8a') or
                (Digest = 'c1c244ecf424957eb07129c4aef27162abc3fc81c18ad6488c5b4dc32f3f5bfd') or
                (Digest = 'ceb9dcc173d4ec0cd8c14275b3de5436fc17221e285f8634c00241aa3f14e8b6') or
                (Digest = 'd8760033eda8c527fd321ade8a5e88f45a144ddb8141543c29652679ea1346f1') or
                (Digest = '08074746d6c18edcf3393fea64591b64d667cf275267695676f356083eefd321') or
                (Digest = 'dd45b5602fc23d7d709117e82b001cf3b007f865987614fca96341772ab27d3c') or
                (Digest = '00eab537eed2904ba2bc28704933c462cc0a84de2d600e43999637c8aa1c4f6a') or
                (Digest = '70da5ee7daf38968897a6af1c7373fe92d7ba659ffcb66944e1d8c32b4e74122');
  except
    Result := False;
  end;
end;
