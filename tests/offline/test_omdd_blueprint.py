"""Offline checks on Rift Lord's Deathtrap Blueprint writer (riftlord/formats/omdd: kismet5,
bpclass5, typedb5, bpwidget5): bytecode and class exports measured on shipped data walk and
write back byte for byte without the game; with Deathtrap installed, shipped Blueprints
round-trip, the dependency rules reproduce shipped packages, and the probe widget compiles,
passes verify() and packs into a container that reads back (retoc verify too, when retoc is
built in the P2 scratch folder).

    python3 test_omdd_blueprint.py
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from riftlord.formats.omdd import bpclass5, kismet5, trace_omdd, zenpackage  # noqa: E402

SCRATCH_PARENT = '/mnt/2tb_pro_a/tmp' if os.path.isdir('/mnt/2tb_pro_a/tmp') else None
RETOC = '/mnt/2tb_pro_a/tmp/deathtrap-maps/retoc-target/release/retoc'
#: W_ModeSelect_TopBar's InitTabs (52 bytes) and one bound-event stub (137 bytes), as shipped.
INIT_TABS = bytes.fromhex('000100000000000000000000000000000000120000000e000000460a0000001d67'
                          '15000016040b530000000c0000000000000000')
BOUND_EVENT = bytes.fromhex(
    '0001000000000000000000000000010000007900000000000000170000000000000001000000010000000800'
    '000094000800000000000000770000000000000000faffffff240000003000000064010000005d0000000200'
    '00000a0000000001000000170000000000000004000000460a0000001df909000016040b5300000008000000'
    '0000000000')
#: Shipped packages round-tripped with the game: widgets, a coordinator, a level script.
SHIPPED = ('/Game/UI/Menus/ModeSelect/W_ModeSelect_TopBar', '/Game/UI/Menus/Codex/BP/W_Button_CodexPip',
           '/Game/Levels/BaseGame/Castle/Onslaughts/BP_Onslaught_Castle_Crunch',
           '/Game/UI/Menus/MainMenu/W_UserInfo')
#: Shipped widget packages whose dependency bundles the rules reproduce exactly.
DEPENDENCY_SAMPLES = ('/Game/UI/Menus/Codex/BP/W_Button_CodexPip', '/Game/UI/Menus/MainMenu/W_UserInfo',
                      '/Game/UI/HUD/AbilityDisplay/W_AbilityDisplaySlot_Secondary')

PROBE_YAML = """name: W_RiftLordCustomMapsProbe
path: /Game/Mods/RiftLord/W_RiftLordCustomMapsProbe
parent: /Script/UMG.UserWidget
script: W_RiftLordCustomMapsProbe.py
root:
  name: Root
  class: /Script/UMG.VerticalBox
  children:
    - slot: {HorizontalAlignment: HAlign_Center, Padding: {Top: 120.0}}
      widget: {name: Title, class: /Script/UMG.TextBlock, variable: true, properties: {Text: Custom Maps}}
    - slot: {HorizontalAlignment: HAlign_Center, Padding: {Top: 16.0}}
      widget:
        name: ClickButton
        class: /Script/UMG.Button
        events: {OnClicked: Clicked}
        children:
          - slot: {Padding: {Left: 24.0, Top: 8.0, Right: 24.0, Bottom: 8.0}}
            widget: {name: ButtonLabel, class: /Script/UMG.TextBlock, properties: {Text: Rift Lord P2 probe}}
"""
PROBE_SCRIPT = '''Clicks: int = 0


def Clicked():
    self.Clicks = self.Clicks + 1
    self.Title.SetText(Text("Custom Maps: clicked " + str(self.Clicks)))


def Describe() -> str:
    return "Rift Lord P2 probe, clicks " + str(self.Clicks)
'''

#: Round 2: the Custom Maps tab and page, importing the game's Blueprints and assets.
TAB_YAML = '# The Custom Maps tab button, laid out as each of W_ModeSelect_TopBar\'s own tabs is:\n# SizeBox (centred) -> ScaleBox (fill) -> W_ButtonBasic with the tab style, selectable,\n# clicked after selection. W_RiftLordCustomMaps inserts it into the bar.\nname: W_RiftLordCustomMapsTab\npath: /Game/Mods/RiftLord/W_RiftLordCustomMapsTab\nparent: /Script/UMG.UserWidget\nroot:\n  name: TabSizeBox\n  class: /Script/UMG.SizeBox\n  children:\n    - slot: {VerticalAlignment: VAlign_Center}\n      widget:\n        name: TabScaleBox\n        class: /Script/UMG.ScaleBox\n        children:\n          - slot: {HorizontalAlignment: HAlign_Fill, VerticalAlignment: VAlign_Fill}\n            widget:\n              name: TabButton\n              class: /Game/UI/Buttons/W_ButtonBasic.W_ButtonBasic_C\n              properties:\n                ButtonText: CUSTOM MAPS\n                Style: "BlueprintGeneratedClass\'/Game/UI/Styles/Button/S_Button_TabMenuTab.S_Button_TabMenuTab_C\'"\n                bSelectable: true\n                bTriggerClickedAfterSelection: true\n'
PAGE_YAML = '# The Custom Maps page of the map selector (W_ModeSelect), a placeholder for round 2: a header,\n# a line of text and one entry row. Its script installs the page and its tab.\nname: W_RiftLordCustomMaps\npath: /Game/Mods/RiftLord/W_RiftLordCustomMaps\nparent: /Script/UMG.UserWidget\nscript: W_RiftLordCustomMaps.py\nroot:\n  name: PageOverlay\n  class: /Script/UMG.Overlay\n  children:\n    - slot: {HorizontalAlignment: HAlign_Fill, VerticalAlignment: VAlign_Fill}\n      widget:\n        name: PageBackground\n        class: /Script/UMG.Image\n        properties:\n          Brush:\n            DrawAs: Box\n            Margin: {Left: 0.1, Top: 0.1, Right: 0.1, Bottom: 0.1}\n            ResourceObject: "Texture2D\'/Game/UI/Menus/MissionStats/UI/stats_bg_Frame.stats_bg_Frame\'"\n    - slot: {Padding: {Left: 60.0, Top: 60.0, Right: 60.0, Bottom: 60.0}, HorizontalAlignment: HAlign_Fill, VerticalAlignment: VAlign_Fill}\n      widget:\n        name: PageBox\n        class: /Script/UMG.VerticalBox\n        children:\n          - slot: {HorizontalAlignment: HAlign_Center}\n            widget:\n              name: Header\n              class: /Script/CommonUI.CommonTextBlock\n              properties:\n                Text: CUSTOM MAPS\n                Style: "BlueprintGeneratedClass\'/Game/UI/Styles/Text/S_Text_MissionSelect_Header.S_Text_MissionSelect_Header_C\'"\n          - slot: {Padding: {Top: 40.0}, HorizontalAlignment: HAlign_Center}\n            widget:\n              name: Intro\n              class: /Script/CommonUI.CommonTextBlock\n              variable: true\n              properties:\n                Text: Maps made with Rift Lord will be listed here.\n                Style: "BlueprintGeneratedClass\'/Game/UI/Styles/Text/S_Text_LobbyList_Text.S_Text_LobbyList_Text_C\'"\n          - slot: {Padding: {Top: 40.0}, HorizontalAlignment: HAlign_Center}\n            widget:\n              name: SampleEntry\n              class: /Game/UI/Buttons/W_ButtonBasic.W_ButtonBasic_C\n              events: {OnButtonBaseClicked: SampleClicked}\n              properties:\n                ButtonText: Sample map (placeholder)\n                Style: "BlueprintGeneratedClass\'/Game/UI/Styles/Button/S_Button_LobbyList.S_Button_LobbyList_C\'"\n'
PAGE_SCRIPT = '"""The Custom Maps page and its tab in the map selector\'s top bar.\n\nInstall(Owner) adds this page to the selector\'s ModeSwitcher and a tab after Standard /\nChallenges / Endurance, registered with the bar as the bar registers its own (Buttons,\nTabIndexToWidgetIndexMap, NumTabs), so the bar\'s own logic selects it: clicks, gamepad tab\ncycling, the cursor and the page switch. Lua calls Install once per W_ModeSelect."""\n\nModeSelect = Blueprint("/Game/UI/Menus/ModeSelect/W_ModeSelect.W_ModeSelect_C")\nTopBar = Blueprint("/Game/UI/Menus/ModeSelect/W_ModeSelect_TopBar.W_ModeSelect_TopBar_C")\nTab = Blueprint("/Game/Mods/RiftLord/W_RiftLordCustomMapsTab.W_RiftLordCustomMapsTab_C")\n\nBar: TopBar\nTabWidget: Tab\nTabIndex: int = -1\nPageIndex: int = -1\nClicks: int = 0\n\n\ndef Install(Owner: ModeSelect) -> bool:\n    if self.TabIndex >= 0:\n        return False\n    self.Bar = Owner.W_ModeSelect_TopBar\n    Switcher = Owner.ModeSwitcher\n    Switcher.AddChild(self)\n    self.PageIndex = Switcher.GetChildrenCount() - 1\n    self.TabWidget = cast(Tab, Create(self, Asset("/Game/Mods/RiftLord/W_RiftLordCustomMapsTab.W_RiftLordCustomMapsTab_C"), GetOwningPlayer()))\n    Panel = cast(HorizontalBox, self.Bar.ChallengesSizeBox.GetParent())\n    TabSlot = Panel.AddChildToHorizontalBox(self.TabWidget)\n    TabSlot.SetSize(SlateChildSize(Value=1.0, SizeRule=1))    # SizeRule 1: ESlateSizeRule::Fill\n    self.Bar.Buttons.append(self.TabWidget.TabButton)\n    self.TabIndex = len(self.Bar.Buttons) - 1\n    Map_Add(self.Bar.TabIndexToWidgetIndexMap, self.TabIndex, self.PageIndex)\n    self.Bar.NumTabs = len(self.Bar.Buttons)\n    self.TabWidget.TabButton.OnButtonBaseClicked.add(self, "TabClicked")\n    self.Bar.On_Tab_Selected.add(self, "TabSelected")\n    self.Bar.Process_Button_State(self.TabWidget.TabButton, self.PageIndex)\n    self.Bar.Update_Cursor_Position()\n    return True\n\n\ndef TabClicked(Button: CommonButtonBase):\n    self.Bar.Set_Target_Tab(self.Bar.Buttons.find(self.TabWidget.TabButton))\n\n\ndef TabSelected(TabIndex: int):\n    self.Bar.Process_Button_State(self.TabWidget.TabButton, self.PageIndex)\n\n\ndef SampleClicked(Button: CommonButtonBase):\n    self.Clicks = self.Clicks + 1\n    self.Intro.SetText(Text("Placeholder entry pressed " + str(self.Clicks) + " times: nothing to launch yet."))\n\n\ndef Describe() -> str:\n    return "tab " + str(self.TabIndex) + ", page " + str(self.PageIndex) + ", bar tabs " + str(self.Bar.NumTabs) + ", bar current tab " + str(self.Bar.CurrentTab)\n'

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))


def offline():
    k = kismet5
    nodes, used = k.read(INIT_TABS[26:40])
    check('a shipped script walks to EndOfScript', used == 14 and nodes[-1].name == 'EndOfScript'
          and [n.name for n in nodes] == ['LocalFinalFunction', 'Return', 'EndOfScript'])
    check('in-memory size: an object reference is 8 bytes, a name 12', k.sizes(nodes) == (14, 18),
          k.sizes(nodes))
    check('the script writes back byte for byte', k.write(nodes) == INIT_TABS[26:40])
    vec = k.Node('VectorConst', [('f64', 1.5), ('f64', -2.0), ('f64', 3.25)])
    check('vectors are doubles', len(k.node_bytes(vec)) == 25
          and k.read(k.node_bytes(vec) + b'\x53')[0][0].ops[1] == ('f64', -2.0))
    field = k.Node('LocalVariable', [('field', ([(7, 0)], 4))])
    check('a field path is count, names, owner; 8 bytes in memory',
          k.node_bytes(field) == bytes.fromhex('00010000000700000000000000' '04000000')
          and k.memory_size(field) == 9)
    sw = k.Node('SwitchValue', [('u16', 1), ('u32', 99), ('expr', k.Node('IntZero')),
                                ('expr', k.Node('IntOne')), ('u32', 50), ('expr', k.Node('True')),
                                ('expr', k.Node('False'))])
    back, _ = k.read(k.node_bytes(sw) + b'\x53')
    check('a switch reads its cases', k.write(back[:1]) == k.node_bytes(sw))
    names = ['?'] * 0x79 + ['ObjectProperty', 'None']
    names[0x77], names[0x17] = 'None', 'Button'
    for label, data in (('a function with no properties', INIT_TABS),
                        ('a bound event with an ObjectProperty parameter', BOUND_EVENT)):
        x = bpclass5.read_struct(data, 2, 'function', names)
        check('class model: %s re-serializes' % label, bpclass5.write_struct(x) == data[2:])
    x = bpclass5.read_struct(BOUND_EVENT, 2, 'function', names)
    p = x.properties[0]
    check('the parameter reads as shipped', p.kind == 'ObjectProperty' and p.property_flags == 0x80094
          and p.element_size == 8 and p.refs == {'property_class': -6})
    check('UStruct, then UFunction: flags and event graph', x.fields['function_flags'] == 0x8000000
          and x.script_memory == 36 and len(x.script) == 48)
    from riftlord.formats.omdd import bpwidget5 as bw
    check('a plain bool false is zero-masked, a bitfield bool is written',
          bw.is_zero(('Bool',), 0) and not bw.is_zero(('Bool',), 0, bitfield=True))
    check('a struct with a schema is never masked whole; a binary one is',
          not bw.is_zero(('Struct', 'Margin'), {}) and bw.is_zero(('Struct', 'Vector2D'), (0.0, 0.0)))
    check('import outers: function -> class -> script package',
          bw.import_outer('/Script/UMG.TextBlock:SetText') == '/Script/UMG.TextBlock'
          and bw.import_outer('/Script/UMG.TextBlock') == '/Script/UMG'
          and bw.import_outer('/Script/UMG') is None)


def with_game(scratch):
    from riftlord.formats.omdd import bpwidget5 as bw, iostore_write, unversioned_write
    g = trace_omdd.game()
    scripts = structs = same_structs = same_scripts = 0
    tree = tree_same = 0
    for name in SHIPPED:
        p = g.package(name)
        types = bpclass5.BlueprintTypes(g)
        for k, e in enumerate(p.exports):
            kind = bpclass5.kind_of_class(p.class_name(k))
            if kind:
                _f, end = p.props_end(k, raw=True)
                x = bpclass5.read_struct(p.blob(k), end - e['at'], kind, p.names)
                structs += 1
                same_structs += bpclass5.write_struct(x) == p.blob(k)[end - e['at']:]
                if x.script:
                    nodes = x.nodes()
                    scripts += 1
                    same_scripts += (kismet5.write(nodes) == x.script
                                     and kismet5.sizes(nodes)[1] == x.script_memory)
            elif 'WidgetBlueprintGeneratedClass' in [p.class_name(j) for j in range(len(p.exports))]:
                cname = types.register_class_of(p, k)
                r = trace_omdd.Props(p, p.data, e['at'], e['at'] + e['size'], raw=True)
                r.types = types
                try:
                    fields = r.struct(cname)
                except trace_omdd.PropError:
                    continue
                tree += 1
                tree_same += unversioned_write.Writer(types).struct(cname, fields) == p.data[e['at']:r.o]
    check('shipped scripts walk, keep their memory size and write back (%d)' % scripts,
          scripts and same_scripts == scripts, '%d of %d' % (same_scripts, scripts))
    check('shipped class and function exports re-serialize (%d)' % structs,
          structs and same_structs == structs, '%d of %d' % (same_structs, structs))
    check('shipped widget objects, Blueprint user widgets included, re-encode (%d)' % tree,
          tree and tree_same == tree, '%d of %d' % (tree_same, tree))
    for name in DEPENDENCY_SAMPLES:
        c, i = g.files[name.lower()]
        z = zenpackage.ZenPackage(c.read(i))
        view = g.package(name)
        got = bw.dependencies(view, z, bpclass5.BlueprintTypes(g))
        want = []
        for first, *counts in z.dep_headers:
            lists, at = [], first
            for n in counts:
                lists.append(z.dep_entries[at:at + n])
                at += n
            want.append(tuple(lists))
        check('dependency rules reproduce %s' % name.rsplit('/', 1)[1],
              [tuple(x) for x in got] == want)
    # the probe
    with open(os.path.join(scratch, 'W_RiftLordCustomMapsProbe.yaml'), 'w') as fh:
        fh.write(PROBE_YAML)
    with open(os.path.join(scratch, 'W_RiftLordCustomMapsProbe.py'), 'w') as fh:
        fh.write(PROBE_SCRIPT)
    z = bw.compile(bw.load(os.path.join(scratch, 'W_RiftLordCustomMapsProbe.yaml')))
    data = z.to_bytes()
    problems = bw.verify(data)
    check('the probe widget compiles and verify() finds nothing', not problems, problems)
    # verify() catches each of three deliberate corruptions
    k = next(j for j, e in enumerate(z.exports) if z.export_name(e) == 'Clicked')
    bad_field = zenpackage.ZenPackage(data)
    x = bpclass5.read_struct(bad_field.exports[k].data, 2, 'function', bad_field.names)
    nodes = x.nodes()
    engine = (bad_field.names.index('Engine'), 0)
    for top in nodes:
        for node in top.walk():
            node.ops = [('field', ([engine], v[1])) if kind == 'field' and v[0] else (kind, v)
                        for kind, v in node.ops]
    x.set_nodes(nodes)
    bad_field.exports[k].data = bad_field.exports[k].data[:2] + bpclass5.write_struct(x)
    bad_size = zenpackage.ZenPackage(data)
    x = bpclass5.read_struct(bad_size.exports[k].data, 2, 'function', bad_size.names)
    x.script_memory += 1
    bad_size.exports[k].data = bad_size.exports[k].data[:2] + bpclass5.write_struct(x)
    bad_order = zenpackage.ZenPackage(data)
    bad_order.bundle = list(reversed(bad_order.bundle))
    found = [bool(bw.verify(p.to_bytes())) for p in (bad_field, bad_size, bad_order)]
    check('verify() catches a wrong field path, a wrong script size and a broken bundle order',
          all(found), found)
    view = trace_omdd.Package(g, z.name, None, None, data)
    classes = sorted(view.class_name(k) for k in range(len(view.exports)))
    check('the probe holds the class, CDO, tree, widgets, slots, binding and two functions',
          classes == sorted(['Button', 'ButtonSlot', 'ComponentDelegateBinding', 'Function', 'Function',
                             'TextBlock', 'TextBlock', 'VerticalBox', 'VerticalBoxSlot', 'VerticalBoxSlot',
                             'W_RiftLordCustomMapsProbe_C', 'WidgetBlueprintGeneratedClass', 'WidgetTree']),
          classes)
    check('exports in shipped order: by class name, then path',
          list(range(len(z.exports))) == sorted(range(len(z.exports)), key=lambda k: (
              view.class_name(k).lower(), z.export_path(k).lower())))
    check('the export bundle keeps every dependency', bw.valid_order(z, [tuple(b) for b in z.bundle]))
    utoc = os.path.join(scratch, 'RiftLordP2Test_P.utoc')
    bw.write_container(utoc, [z])
    c = trace_omdd.Container(utoc)
    path = '../../../OMDD/Content/Mods/RiftLord/W_RiftLordCustomMapsProbe.uasset'
    check('the container reads back', path in c.files and c.read(c.files[path]) == data)
    hi = next(i for i, x in enumerate(c.ids) if x[11] == iostore_write.CHUNK_HEADER)
    header = iostore_write.ContainerHeader.parse(c.read(hi))
    check('the container header lists the package', zenpackage.package_id(z.name) in header.packages)
    if os.path.exists(RETOC):
        r = subprocess.run([RETOC, 'verify', utoc], capture_output=True, text=True)
        check('retoc verify', r.returncode == 0, r.stdout[-200:] + r.stderr[-200:])
    else:
        print('skipped retoc verify: no retoc at %s' % RETOC)
    custom_maps(g, scratch)


def custom_maps(g, scratch):
    """Round 2: two packages built together, importing the game's Blueprints, styles and a
    texture, and each other."""
    from riftlord.formats.omdd import bpwidget5 as bw, typedb5
    for name, text in (('W_RiftLordCustomMapsTab.yaml', TAB_YAML), ('W_RiftLordCustomMaps.yaml', PAGE_YAML),
                       ('W_RiftLordCustomMaps.py', PAGE_SCRIPT)):
        with open(os.path.join(scratch, name), 'w') as fh:
            fh.write(text)
    tab, page = bw.compile_all([bw.load(os.path.join(scratch, n)) for n in
                                ('W_RiftLordCustomMapsTab.yaml', 'W_RiftLordCustomMaps.yaml')])
    for z in (tab, page):
        problems = bw.verify(z.to_bytes(), bw.compile_all.db)
        check('%s compiles and verify() finds nothing' % z.name.rsplit('/', 1)[1], not problems, problems)
    view = g.package(page.name)
    nulls = sum(1 for v in page.imports if v == zenpackage.NULL_INDEX)
    check('a null import per imported package, as shipped', nulls == len(page.imported_packages) == 8,
          (nulls, page.imported_packages))
    check('imported packages sorted by package id, as shipped',
          [n for n, _ in page.imported_packages] == sorted((n for n, _ in page.imported_packages),
                                                           key=zenpackage.package_id))
    targets = {}
    for i, v in enumerate(page.imports):
        if v != zenpackage.NULL_INDEX and v >> 62 == zenpackage.KIND_PACKAGE:
            got = view.import_target(i)
            targets[typedb5.export_path(*got)] = got[0].class_name(got[1])
    check('every package import resolves, the sibling tab class included',
          len(targets) == sum(1 for v in page.imports if v >> 62 == zenpackage.KIND_PACKAGE)
          and '/Game/Mods/RiftLord/W_RiftLordCustomMapsTab.W_RiftLordCustomMapsTab_C' in targets, targets)
    classes = [x for x, c in targets.items() if c.endswith('GeneratedClass')]
    check('each Blueprint class import brings its default object, as shipped',
          all('%s.Default__%s' % tuple(x.rsplit('.', 1)) in targets for x in classes), classes)
    k = next(j for j, e in enumerate(view.exports) if e['name'] == 'Install')
    _f, end = view.props_end(k, raw=True)
    x = bpclass5.read_struct(view.blob(k), end - view.exports[k]['at'], 'function', view.names)
    text = bpclass5.Cursor(b'', 0, view.names).text
    by_name = sorted({text(n.ops[0][1]) for top in x.nodes() for n in top.walk()
                      if n.name == 'LocalVirtualFunction'})
    check("the bar's Blueprint functions are called by name, as W_ModeSelect calls its bar's",
          by_name == ['Process Button State', 'Update Cursor Position'], by_name)
    ship = g.package('/Game/UI/Menus/ModeSelect/W_ModeSelect_TopBar')
    mine = g.package(tab.name)
    types = bpclass5.BlueprintTypes(g)

    def layout(p, name):
        j = next(i for i, e in enumerate(p.exports) if e['name'] == name)
        e = p.exports[j]
        r = trace_omdd.Props(p, p.data, e['at'], e['at'] + e['size'], raw=True)
        r.types = types
        return [(i, zero) for i, zero, _v in r.struct(types.register_class_of(p, j)).entries]
    check("the tab button holds the shipped tab button's properties",
          layout(ship, 'Standardbutton') == layout(mine, 'TabButton'))
    utoc = os.path.join(scratch, 'RiftLordP2Test2_P.utoc')
    bw.write_container(utoc, [tab, page])
    c = trace_omdd.Container(utoc)
    check('both packages read back from one container',
          all(c.read(c.files['../../../OMDD/Content/%s.uasset' % z.name[6:]]) == z.to_bytes()
              for z in (tab, page)))


def main():
    scratch = tempfile.mkdtemp(prefix='omdd-blueprint-test-', dir=SCRATCH_PARENT)
    try:
        offline()
        why = trace_omdd.missing()
        if why:
            print('skipped the shipped-game checks: %s' % why)
        else:
            with_game(scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    bad = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print('%s %s%s' % ('ok  ' if ok else 'FAIL', name, '' if ok else ': %s' % (detail,)))
    print('%d of %d checks passed' % (len(checks) - len(bad), len(checks)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
