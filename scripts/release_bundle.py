"""Publish audited program files while preserving the local application's data."""
import os
import hashlib
from pathlib import Path
import shutil
import sys
import sysconfig
import tempfile
from zipfile import ZipFile,ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.build_installer import stage_payload,POLICY_FILES

# Only byte-identical shipped defaults may be replaced; user edits stay intact.
UPGRADABLE_POLICY_HASHES={
    'agent.md':{'6bf3fe847e8d8fdc9d9ad659586f75f485c58de7de88265f1af66c8543cf6b7a','46b1a74c632fc7dea302b11ee442bc71edcea70f3d44cae4e1c58bf817a91258','512c07aa57c2ce91c17c396202b762590339ea05d23d30819cbfab167f7f4fd4','e03fe9fc41cb1ef044e46bb95cb165bf82d06f1cf5bfb023a309df6d9aa8bfc3','34479b46f01974036df4a7c3954c3c94ee7a72f316c3841d407792d7863de68f','9b3e2a0de0e86a39f2d117b149abb01597a207be47b1b9bed153d5a3f8a9818e','8df720b1875f053a82775dd10554f37ffee6538626761bc2f1ebaf1369b6f911','099ac1cf6251d40c5276244ab3558d05bb26c689521ecb5c80bfe58af4341c72',
                '346c87194ee3d85d299d8a1aa1d2de411440e8d69505f3388fcc68619e6bb62d','a0a81341c23fcb39d22760e5dca99fc5704fd6e3797807724e343c99d23d4895',
                '05a950bc8bc3acf018c5a1ad1b6f8f1fed56ab2e7d64dbf2bcfcb041e1cf5159','1c5c2f41ab33481dbc7138598d0295e75dba374a3cf1e882fd345da5ae0d39a3'},
    'library_agent.md':{'9855c018f8ac766244b236f061b83be3c53aea4ef35ae2d3b2ad00b42318ba15','c1576708a9b9fe8b47d5b056918a63cdd8e1381633aba740aa1707b92b13b06f','38f5f3976d8c147f28daaa049b40225dd8fbb77cd27b83e67d90b3aafce2e91d','95be4dcb69600bf7fab4072a76c672b238986551e051437ebcef512e66fbd9c8',
                        '97ccdf2923a7626a9bfe1c9638cae7b9b6f045a72106352bfaa565c267c86cb0','ec53cf3647a7207db870a65723480ac89da0a4fad5a855c642d9712a2aa2ed72',
                        'e715906f33988597fb878a9153c2e8628fb1e0d1a8db18863d3e9a488c32fb78','baecf1d773801e3be89378f804754a8db830bfe1751650666f18b6964c64c8fa'},
    'experiment_agent.md':{'4e503b853050b81c3574835eebb202f093362a8e0f28ec18b1ac769801788323','00eab537eed2904ba2bc28704933c462cc0a84de2d600e43999637c8aa1c4f6a','70da5ee7daf38968897a6af1c7373fe92d7ba659ffcb66944e1d8c32b4e74122',
    'bc7a74dd4710453fbced7f3a0a2b0481d2c97bd3a3315a0a6cd3b54fa41e2d8a',
    'c1c244ecf424957eb07129c4aef27162abc3fc81c18ad6488c5b4dc32f3f5bfd',
    'ceb9dcc173d4ec0cd8c14275b3de5436fc17221e285f8634c00241aa3f14e8b6',
    'd8760033eda8c527fd321ade8a5e88f45a144ddb8141543c29652679ea1346f1',
    '08074746d6c18edcf3393fea64591b64d667cf275267695676f356083eefd321',
    'dd45b5602fc23d7d709117e82b001cf3b007f865987614fca96341772ab27d3c',
    '870d480f16362c1de16e854056ce9adb976aebc91306e567c135ebe75d7c9de5',
    '2cbfdc7015e64d39dc1114471e21c926518447ec973f473d958a57352a5249ef'}}


def publish_bundle(bundle,destination,archive,packages):
    destination,archive=Path(destination),Path(archive)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='release-audit-',dir=destination.parent) as folder:
        stage=Path(folder)/'MatKnow'
        stage_payload(bundle,stage,packages)
        files=[file for file in stage.rglob('*') if file.is_file()]
        packed=Path(folder)/'release.zip'
        with ZipFile(packed,'w',ZIP_DEFLATED) as zip:
            for source in files:zip.write(source,Path('MatKnow')/source.relative_to(stage))
        for source in files:
            relative=source.relative_to(stage);target=destination/relative
            if len(relative.parts)==1 and relative.name in POLICY_FILES and target.exists():
                if target.stat().st_size>65536 or hashlib.sha256(target.read_bytes()).hexdigest() not in UPGRADABLE_POLICY_HASHES.get(relative.name,set()):continue
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        os.replace(packed,archive)


if __name__=='__main__':
    publish_bundle(ROOT/'build/release-bundle/MatKnow',ROOT/'dist/MatKnow',
                   ROOT/'dist/MatKnow-Windows-x64.zip',Path(sysconfig.get_path('purelib')))
    print('Published program files without replacing local app-data; ZIP contains only audited release files.')
