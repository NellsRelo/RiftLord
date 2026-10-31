"""The Chandelier: an environmental trap that hangs on a chain, falls when used or shot (on
the chandelier or its chain), breaks on the floor below dealing physical and fire damage,
its candles going out, then fades away and never comes back.

It is carried in each mission that places it: a kit of packages under KIT_ROOT, built on
this machine and copied byte for byte into the mission pak.

* **BP_RiftLordChandelier** (bpkit, SCRIPT): a child of the interactable trap base, as the
  falling boom barrel is. Its TrapProtos row (Tables/RiftLordChandelier_TrapProtos, a copy
  of FallingBoomBarrel_ENV) has custom interaction, so the Interact key and shots reach
  BP_CustomInteraction on the server; the row's ability (the barrel's) never runs. The
  server traces the floor below, sets `FellAt` and `LandZ` and the RepNotify `Fallen`;
  every machine then drops the chandelier under gravity from those, so the host decides
  and clients see the same fall. On landing the candles' flames and lights go out, the
  break effect and sound play, the server deals the damage, and after FadeDelay the
  chandelier and its candles wear masked copies of their materials whose Opacity falls;
  the server then removes it.
* **The chain** is the Hanging Chain compound's (`chandelier_trap` in compounds.py), as a
  shipped boom barrel hangs from its level's chain meshes. The build writes the
  chandelier's `ChainLength` from its group's post, and the Blueprint puts a box of that
  length around the chain, so a shot on the chain drops it.
* **Shots**: the interactable trap base binds only its MeshComponent's hits to its native
  `OnHit` (a shootable trap hit by a player projectile that may interact with traps calls
  Interact); a hitscan hit reaches its TakeDamage, which does the same for a player causer.
  The chandelier's parts are its own components, so it binds the body's and the chain
  box's hits to `OnHit` as the base binds its mesh's.
* **Placement**: a shipped boom barrel placement retargeted to the class
  (kitactors._chest_view), its origin at the chandelier's hook (HOOK), at SCALE.

    python3 -m riftlord.mapeditor.chandelier --check DIR     # build, place and gate offline
"""

import argparse
import collections
import hashlib
import json
import math
import os
import struct

MOD_KEY = 'RiftLordChandelier'
KIT_ROOT = '/Game/Mods/%s' % MOD_KEY
CLASS_NAME = 'BP_RiftLordChandelier'
CLASS = CLASS_NAME + '_C'
PACKAGE = '%s/%s' % (KIT_ROOT, CLASS_NAME)
PARENT = '/Game/Traps/_Base/BP/BP_OMDTrapInteractable_Base.BP_OMDTrapInteractable_Base_C'
TABLE = '%s/Tables/%s_TrapProtos' % (KIT_ROOT, MOD_KEY)
ROW = '%s_Chandelier' % MOD_KEY
GUID = hashlib.md5(('riftlord.%s.TrapProtos.%s' % (MOD_KEY, ROW)).encode()).digest()
#: The shipped row the chandelier's is a copy of, and the placement it is retargeted from.
TEMPLATE_ROW = 'FallingBoomBarrel_ENV'
DONOR = 'BP_FallingBoomBarrel_C'
NAME = 'Chandelier'
INTERACT = 'Drop The Chandelier'
DESCRIPTION = 'When used or shot, the chandelier falls, crushing and burning whatever is below.'
MRA_TEXTURE = '%s/T_RiftLordChandelier_MRA' % KIT_ROOT
FADE_MIC = '%s/MI_RiftLordChandelier_Fade' % KIT_ROOT
CANDLE_FADE_MIC = '%s/MI_RiftLordCandle_Fade' % KIT_ROOT

MESH = 'SM_Chandelier_01'
MESH_PATH = '/Game/Proto/Environments/Medieval_Furniture_Pack2/Meshes/SM_Chandelier_01'
CANDLE = 'candle02'
CANDLE_PATH = '/Game/Proto/Environments/FantasyDungeon/meshes/Chandelier/candle02'
#: The chandelier's textures: base colour, normal, and its ORM mask (R ambient occlusion,
#: G roughness, B metallic), which the fade copy reads as MRA, channels swapped.
BC_TEXTURE = '/Game/Proto/Environments/Medieval_Furniture_Pack2/Textures/T_Chandelier_01_BC'
ORM_TEXTURE = '/Game/Proto/Environments/Medieval_Furniture_Pack2/Textures/T_Chandelier_01_M'
NM_TEXTURE = '/Game/Proto/Environments/Medieval_Furniture_Pack2/Textures/T_Chandelier_01_N'
CANDLE_BC = '/Game/Proto/Environments/FantasyDungeon/Textures/Chandelier/candle_D'
CANDLE_NM = '/Game/Proto/Environments/FantasyDungeon/Textures/Chandelier/candle_N'
#: The masked trap master's shipped flat MRA (no metal, mid roughness, no occlusion).
DULL_MRA = '/Game/MasterMaterials/Shared/Dull_MRA'
MRA_SIZE = 1024

#: The flame on each candle (the wall torch's), the break where the chandelier lands (the
#: Pounder's floor impact), the fire it spills, and the sounds: the falling boom barrel's
#: trigger when it drops, the Pounder's impact when it lands.
FLAME_SYSTEM = '/Game/Proto/Environments/CastleOrder/Torches/FX/P_Torch_Fire'
BREAK_SYSTEM = '/Game/Traps/Pounder/FX/P_PounderHit'
FIRE_SYSTEM = '/Game/Proto/Weapons/FlameBracers/FX/P_FireballExplosion01'
DROP_SOUND = '/Game/Traps/FallingBoomBarrel/Audio/FallingBB_Triggered_SC'
LAND_SOUND = '/Game/Traps/Pounder/Audio/Pounder_Activate_Impact_SC'

# -- measured from SM_Chandelier_01 and candle02 (measure() reads them again) -------------
#: The top of the chandelier's hanging loop, in mesh space: the actor origin.
HOOK = (-0.21, 0.0, 127.63)
#: The mesh's lowest point above its origin, and half its height.
BOTTOM = -0.08
HALF_HEIGHT = 63.86
#: Half its width (the larger horizontal extent).
HALF_WIDTH = 70.04
#: The chandelier is placed at this uniform scale (the user: three times as wide); its
#: candles, flames and lights ride on it, so they scale with it.
SCALE = 3.0
#: The twelve sconce pans a candle stands on: centre and top, mesh space.
SCONCES = ((-36.51, 0.02, 64.33), (-0.02, -36.51, 64.33), (0.02, 36.51, 64.33),
           (36.51, -0.02, 64.33),
           (-61.53, 0.02, 26.55), (-43.52, -43.5, 26.55), (-43.5, 43.52, 26.55),
           (-0.02, -61.53, 26.55), (0.02, 61.53, 26.55), (43.5, -43.52, 26.55),
           (43.52, 43.5, 26.55), (61.53, -0.02, 26.55))
#: The candle's wick (top centre) and its mean width.
CANDLE_TIP = (0.23, -0.15, 25.28)
CANDLE_WIDTH = 6.87
#: The wall torch's flame sprite (P_Torch_Fire's fire emitter StartSize X, 40..50): its mean
#: width. The flame is scaled so it is as wide as the candle.
TORCH_FLAME_WIDTH = 45.0
FLAME_SCALE = round(CANDLE_WIDTH / TORCH_FLAME_WIDTH, 3)

# -- numbers from shipped content -----------------------------------------------------
#: FallingBoomBarrel_PBP: Base Damage 800, Explosion Radius 600, Chance to gib 1, impulse on
#: death 100000, Physical; its projectile's collision sphere radius 100.
BARREL_DAMAGE = 800.0
BARREL_RADIUS = 600.0
BARREL_BODY_RADIUS = 100.0
BARREL_GIB = 1.0
BARREL_IMPULSE = 100000.0
#: The barrel's damage split evenly between physical and fire; its blast reach scaled by the
#: chandelier's half width against the barrel's body radius.
PHYSICAL_DAMAGE = BARREL_DAMAGE / 2.0
FIRE_DAMAGE = BARREL_DAMAGE / 2.0
DAMAGE_RADIUS = round(BARREL_RADIUS * HALF_WIDTH * SCALE / BARREL_BODY_RADIUS, 1)
#: BP_Chandelier_Normal's light (Intensity 1, AttenuationRadius 1400, falloff exponent 8,
#: colour 255,176,55), shared between the twelve candles.
SHIPPED_LIGHT = {'intensity': 1.0, 'radius': 1400.0, 'falloff': 8.0, 'colour': (255, 176, 55)}
CANDLE_INTENSITY = round(SHIPPED_LIGHT['intensity'] / len(SCONCES), 4)
#: DefaultEngine.ini DefaultGravityZ.
GRAVITY = 980.0
#: The Hanging Chain's measured parts (compounds.py): Chain_Short's half width and the chain
#: pivot under its post. A chain counts as the chandelier's within this of its hook.
CHAIN_HALF_WIDTH = 7.56
CHAIN_MARGIN = 2.0
CHAIN_IN_POST_Z = 25.6
CHAIN_XY_TOLERANCE = 30.0
#: The shipped boom barrels' hook height over the floor (q3 p50), where a new chain hangs one.
HANG_ABOVE = 1130.5
#: The tallest enemy: capsule half height 180 (Ogres, Trolls, bosses; AICharacterProtos
#: classes' CollisionCylinder), twice. A chandelier's hook must hang at least this plus its
#: own height over the floor so it clears every enemy (HANG_ABOVE does).
TALLEST_ENEMY = 360.0
MIN_HOOK_ABOVE = round(TALLEST_ENEMY + (HOOK[2] - BOTTOM) * SCALE, 1)
#: The collision profile of the chandelier and its chain box: the shipped trap meshes'
#: (BP_OMDTrap_Base MeshComponent). It blocks the OMDProjectile channel, which player
#: projectiles (profile OMDPlayerProjectile) and the hitscan weapon (HandGatling_Primary_PBP
#: FireHitScan traceProfile OMDProjectile) both sweep, and their profiles block it in turn
#: (DefaultEngine.ini: neither overrides OMDTrapMesh, whose default is Block).
HIT_PROFILE = 'OMDTrapMesh'
#: The Map Editor's compound (compounds.COMPOUNDS) and hung item (compounds.HUNG) key.
COMPOUND = 'chandelier_trap'
#: Links in a new chain: the Hanging Chain's default (compounds.CHAIN_LINKS).
CHAIN_LINKS = 6
#: How far below itself a chandelier looks for the floor (the highest shipped barrel hangs
#: 2551 uu over its floor).
FALL_REACH = 10000.0

#: Bump when anything the kit is built from changes.
KIT_VERSION = 2

SCRIPT = '''"""The Chandelier (Rift Lord): an environmental trap hanging on a chain. Interact with it, or
shoot it or its chain, and it falls, breaks on the floor below, deals physical and fire damage
there, its candles go out, and it fades away. It never comes back."""

ChainLength: float = 0.0           # uu of chain above the hook that a shot drops it from (0: none)
ChainHalfWidth: float = {chain_half}       # half width of the chain's hit box, uu
Gravity: float = {gravity}            # uu/s/s
FallStep: float = 0.016            # seconds between fall updates
LateSeconds: float = 1.0           # a fall seen this late on a joining machine plays no effects
PhysicalDamage: float = {physical}      # dealt where it lands, Physical
FireDamage: float = {fire}          # dealt where it lands, Fire
DamageRadius: float = {radius}        # uu around where it lands
GibChance: float = {gib}             # chance a kill gibs
DeathImpulse: float = {impulse}     # impulse on a body it kills
CandleIntensity: float = {intensity}    # each candle's light, unitless brightness
CandleRadius: float = {light_radius}       # each candle's light reach, uu
CandleFalloff: float = {falloff}          # each candle's light falloff exponent
FlameScale: float = {flame_scale}          # the torch flame's scale on a candle
Scale: float = {scale}               # the chandelier's uniform scale
FadeDelay: float = 2.0             # seconds on the floor before it fades
FadeSeconds: float = 1.0           # the fade
FadeStep: float = 0.03             # seconds between fade updates

Body: StaticMeshComponent
ChainBox: BoxComponent
Candles: list[StaticMeshComponent]
Flames: list[ParticleSystemComponent]
Lights: list[PointLightComponent]
Ghost: MaterialInstanceDynamic
CandleGhost: MaterialInstanceDynamic
RestZ: float = 0.0
LandBodyZ: float = 0.0
Falling: bool = False
Landed: bool = False
FadeTime: float = 0.0
Skip: list[Actor]

Fallen: RepNotify[bool]
LandZ: Replicated[float]
FellAt: Replicated[float]


@override
def ReceiveBeginPlay():
    super().ReceiveBeginPlay()
    Hung = MakeTransform(MakeVector({hook}), MakeRotator(0.0, 0.0, 0.0), MakeVector(self.Scale, self.Scale, self.Scale))
    self.Body = cast(StaticMeshComponent, self.AddComponentByClass(StaticMeshComponent, True, Hung, False))
    self.Body.SetMobility(EComponentMobility.Movable)
    self.Body.SetStaticMesh(Asset("StaticMesh'{mesh}'"))
    self.Body.K2_AttachToComponent(self.MeshComponent, "None", EAttachmentRule.KeepRelative, EAttachmentRule.KeepRelative, EAttachmentRule.KeepRelative, False)
    self.Body.K2_SetRelativeLocation(MakeVector({hook}), False, Moved, False)
    self.Body.SetRelativeScale3D(MakeVector(self.Scale, self.Scale, self.Scale))
    self.Body.SetCollisionProfileName("{profile}", True)
    self.Body.SetCollisionResponseToChannel(ECollisionChannel.ECC_GameTraceChannel4, ECollisionResponse.ECR_Ignore)
    Trap = cast(OMDTrapInteractable, self)
    self.Body.OnComponentHit.add(Trap, "OnHit")
{sconces}    if self.ChainLength > 0.0:
        Up = MakeTransform(MakeVector(0.0, 0.0, self.ChainLength / 2.0), MakeRotator(0.0, 0.0, 0.0), MakeVector(1.0, 1.0, 1.0))
        self.ChainBox = cast(BoxComponent, self.AddComponentByClass(BoxComponent, True, Up, False))
        self.ChainBox.K2_AttachToComponent(self.MeshComponent, "None", EAttachmentRule.KeepRelative, EAttachmentRule.KeepRelative, EAttachmentRule.KeepRelative, False)
        self.ChainBox.K2_SetRelativeLocation(MakeVector(0.0, 0.0, self.ChainLength / 2.0), False, Moved, False)
        self.ChainBox.SetBoxExtent(MakeVector(self.ChainHalfWidth, self.ChainHalfWidth, self.ChainLength / 2.0), False)
        self.ChainBox.SetCollisionProfileName("{profile}", True)
        self.ChainBox.SetCollisionResponseToChannel(ECollisionChannel.ECC_GameTraceChannel4, ECollisionResponse.ECR_Ignore)
        self.ChainBox.OnComponentHit.add(Trap, "OnHit")
    self.GeneratedBoxCollision.SetCollisionEnabled(ECollisionEnabled.NoCollision)
    Rest = self.Body.K2_GetComponentLocation()
    self.RestZ = Rest.Z
    if self.Fallen:
        self.StartFall()


def AddCandle(At: Vector):
    """A candle on the sconce at `At` (chandelier space), with its flame and its light."""
    Stand = MakeTransform(At, MakeRotator(0.0, 0.0, 0.0), MakeVector(1.0, 1.0, 1.0))
    Candle = cast(StaticMeshComponent, self.AddComponentByClass(StaticMeshComponent, True, Stand, False))
    Candle.SetMobility(EComponentMobility.Movable)
    Candle.SetStaticMesh(Asset("StaticMesh'{candle}'"))
    Candle.SetCollisionEnabled(ECollisionEnabled.NoCollision)
    Candle.K2_AttachToComponent(self.Body, "None", EAttachmentRule.KeepRelative, EAttachmentRule.KeepRelative, EAttachmentRule.KeepRelative, False)
    Candle.K2_SetRelativeLocation(At, False, Moved, False)
    self.Candles.append(Candle)
    Tip = MakeTransform(MakeVector({tip}), MakeRotator(0.0, 0.0, 0.0), MakeVector(self.FlameScale, self.FlameScale, self.FlameScale))
    Flame = cast(ParticleSystemComponent, self.AddComponentByClass(ParticleSystemComponent, True, Tip, False))
    Flame.K2_AttachToComponent(Candle, "None", EAttachmentRule.KeepRelative, EAttachmentRule.KeepRelative, EAttachmentRule.KeepRelative, False)
    Flame.K2_SetRelativeLocation(MakeVector({tip}), False, Moved, False)
    Flame.SetRelativeScale3D(MakeVector(self.FlameScale, self.FlameScale, self.FlameScale))
    Flame.SetTemplate(Asset("ParticleSystem'{flame}'"))
    self.Flames.append(Flame)
    Glow = MakeTransform(MakeVector({tip}), MakeRotator(0.0, 0.0, 0.0), MakeVector(1.0, 1.0, 1.0))
    Light = cast(PointLightComponent, self.AddComponentByClass(PointLightComponent, True, Glow, True))
    Light.bUseInverseSquaredFalloff = False
    Light.SetMobility(EComponentMobility.Movable)
    self.FinishAddComponent(Light, True, Glow)
    Light.K2_AttachToComponent(Candle, "None", EAttachmentRule.KeepRelative, EAttachmentRule.KeepRelative, EAttachmentRule.KeepRelative, False)
    Light.K2_SetRelativeLocation(MakeVector({tip}), False, Moved, False)
    Light.SetCastShadows(False)
    Light.SetLightFalloffExponent(self.CandleFalloff)
    Light.SetAttenuationRadius(self.CandleRadius)
    Light.SetIntensity(self.CandleIntensity)
    Light.SetLightColor(LinearColor({colour}), True)
    self.Lights.append(Light)


@override
def BP_CustomInteraction(Pawn: Pawn):
    """The Interact key or a shot, on the server: the chandelier falls."""
    if self.Fallen:
        return
    Here = self.Body.K2_GetComponentLocation()
    self.Skip.clear()
    self.Skip.append(self)
    self.LandZ = Here.Z + {bottom} - {reach}
    if LineTraceSingle(Start=MakeVector(Here.X, Here.Y, Here.Z + {bottom}), End=MakeVector(Here.X, Here.Y, Here.Z + {bottom} - {reach}), TraceChannel=ETraceTypeQuery.TraceTypeQuery1, bTraceComplex=False, ActorsToIgnore=self.Skip, DrawDebugType=0, TraceColor=LinearColor(1.0, 0.0, 0.0, 1.0), TraceHitColor=LinearColor(0.0, 1.0, 0.0, 1.0), DrawTime=5.0, OutHit=Under, bIgnoreSelf=True):
        BreakHitResult(Under, Blocking, Initial, Time, Distance, Location, Impact, Normal, ImpactNormal, PhysMat, HitActor, HitComponent, Bone, Item, Element, Face, TraceStart, TraceEnd)
        self.LandZ = Impact.Z
    self.Skip.clear()
    self.FellAt = GetGameState(self).GetServerWorldTimeSeconds()
    self.Fallen = True


@override
def BP_CanInteract(Pawn: Pawn) -> bool:
    """Only a hanging chandelier takes the Interact key (and shows its prompt)."""
    return not self.Fallen


def OnRep_Fallen():
    """On every machine: the chandelier starts to fall."""
    if self.Fallen and IsValid(self.Body):
        self.StartFall()


def StartFall():
    """Nothing takes it for a target any more; the drop sound; the fall, from FellAt."""
    if self.Falling:
        return
    self.Falling = True
    self.LandBodyZ = self.LandZ - {bottom}
    self.Body.SetCollisionEnabled(ECollisionEnabled.NoCollision)
    if IsValid(self.ChainBox):
        self.ChainBox.SetCollisionEnabled(ECollisionEnabled.NoCollision)
    if GetGameState(self).GetServerWorldTimeSeconds() - self.FellAt < self.LateSeconds:
        PlaySoundAtLocation(Asset("SoundCue'{drop_sound}'"), self.Body.K2_GetComponentLocation(), MakeRotator(0.0, 0.0, 0.0))
    K2_SetTimer(self, "Fall", self.FallStep, True)
    self.Fall()


def Fall():
    """The chandelier's height now: dropped from rest under gravity since FellAt."""
    if self.Landed:
        return
    Since = FMax(0.0, GetGameState(self).GetServerWorldTimeSeconds() - self.FellAt)
    Z = self.RestZ - 0.5 * self.Gravity * Since * Since
    Here = self.Body.K2_GetComponentLocation()
    if Z > self.LandBodyZ:
        self.Body.K2_SetWorldLocation(MakeVector(Here.X, Here.Y, Z), False, Moved, False)
        return
    self.Body.K2_SetWorldLocation(MakeVector(Here.X, Here.Y, self.LandBodyZ), False, Moved, False)
    K2_ClearTimer(self, "Fall")
    self.Land(Since)


def Land(Since: float):
    """On the floor: the candles go out, it breaks, the server deals the damage, then it fades."""
    self.Landed = True
    for Flame in self.Flames:
        Flame.Deactivate()
    for Light in self.Lights:
        Light.SetVisibility(False, False)
    Here = self.Body.K2_GetComponentLocation()
    Centre = MakeVector(Here.X, Here.Y, self.LandZ + {half_height})
    Drop = Sqrt(2.0 * FMax(0.0, self.RestZ - self.LandBodyZ) / self.Gravity)
    if Since < Drop + self.LateSeconds:
        SpawnEmitterAtLocation(Asset("ParticleSystem'{break_fx}'"), MakeVector(Here.X, Here.Y, self.LandZ), MakeRotator(0.0, 0.0, 0.0), MakeVector(1.0, 1.0, 1.0))
        SpawnEmitterAtLocation(Asset("ParticleSystem'{fire_fx}'"), Centre, MakeRotator(0.0, 0.0, 0.0), MakeVector(1.0, 1.0, 1.0))
        PlaySoundAtLocation(Asset("SoundCue'{land_sound}'"), Centre, MakeRotator(0.0, 0.0, 0.0))
    if IsServer(self):
        self.Skip.clear()
        self.Skip.append(self)
        OMDGameplayStatics.ApplyRadialDamage(worldContextObject=self, baseDamage=self.PhysicalDamage, chanceToGib=self.GibChance, deathAnimation=0, origin=Centre, outerRadius=self.DamageRadius, eventInstigator=None, damageCauser=self, ignoreActors=self.Skip, damageTypeClass=OMDDamageType_Physical, impulseForceOnDeath=self.DeathImpulse, impulseForceFalloff=0.0, hitDirectionOverride=MakeVector(0.0, 0.0, 0.0), hitDirectionOriginOffset=MakeVector(0.0, 0.0, 0.0), minimumDamage=0.0, innerRadius=0.0, damageFalloff=0.0, damagePreventionChannel=ECollisionChannel.ECC_Visibility, ignoreTeams=False, dotDamage=False)
        OMDGameplayStatics.ApplyRadialDamage(worldContextObject=self, baseDamage=self.FireDamage, chanceToGib=self.GibChance, deathAnimation=0, origin=Centre, outerRadius=self.DamageRadius, eventInstigator=None, damageCauser=self, ignoreActors=self.Skip, damageTypeClass=OMDDamageType_Fire, impulseForceOnDeath=self.DeathImpulse, impulseForceFalloff=0.0, hitDirectionOverride=MakeVector(0.0, 0.0, 0.0), hitDirectionOriginOffset=MakeVector(0.0, 0.0, 0.0), minimumDamage=0.0, innerRadius=0.0, damageFalloff=0.0, damagePreventionChannel=ECollisionChannel.ECC_Visibility, ignoreTeams=False, dotDamage=False)
        self.Skip.clear()
        self.Report(Since, self.LandZ)
    K2_SetTimer(self, "StartFade", self.FadeDelay, False)


def Report(Since: float, Floor: float):
    """Empty: a probe hooks it (the server's landing: seconds since the drop, floor height)."""
    pass


def StartFade():
    """The chandelier and its candles in their masked copies, whose Opacity then falls to 0."""
    self.Ghost = CreateDynamicMaterialInstance(Asset("MaterialInstanceConstant'{fade_mic}'"), "None")
    self.Body.SetMaterial(0, self.Ghost)
    self.CandleGhost = CreateDynamicMaterialInstance(Asset("MaterialInstanceConstant'{candle_fade_mic}'"), "None")
    for Candle in self.Candles:
        Candle.SetMaterial(0, self.CandleGhost)
    self.FadeTime = 0.0
    K2_SetTimer(self, "Fade", self.FadeStep, True)


def Fade():
    """Opacity down to 0; then the server removes the chandelier (every machine follows)."""
    self.FadeTime += self.FadeStep
    Left = FMax(0.0, 1.0 - self.FadeTime / self.FadeSeconds)
    self.Ghost.SetScalarParameterValue("Opacity", Left)
    self.CandleGhost.SetScalarParameterValue("Opacity", Left)
    if self.FadeTime < self.FadeSeconds:
        return
    K2_ClearTimer(self, "Fade")
    self.Body.SetVisibility(False, True)
    if IsServer(self):
        self.K2_DestroyActor()


@override
def ChangeTrapTriggerDisplay(NewIsActive: bool):
    # the trap's trigger-area square (shown in the build phase) stays hidden
    self.TriggerDisplay.SetVisibility(False, False)


@override
def BP_CanSell() -> bool:
    return False
'''


def _num(v):
    return repr(float(v))


def _vec(v):
    return ', '.join(_num(c) for c in v)


def _obj(path):
    return '%s.%s' % (path, path.rsplit('/', 1)[-1])


def script():
    """The Blueprint's bpkit source."""
    colour = tuple(round(c / 255.0, 4) for c in SHIPPED_LIGHT['colour']) + (1.0,)
    return SCRIPT.format(
        chain_half=_num(CHAIN_HALF_WIDTH + CHAIN_MARGIN), gravity=_num(GRAVITY),
        physical=_num(PHYSICAL_DAMAGE), fire=_num(FIRE_DAMAGE), radius=_num(DAMAGE_RADIUS),
        gib=_num(BARREL_GIB), impulse=_num(BARREL_IMPULSE), intensity=_num(CANDLE_INTENSITY),
        light_radius=_num(SHIPPED_LIGHT['radius']), falloff=_num(SHIPPED_LIGHT['falloff']),
        flame_scale=_num(FLAME_SCALE),
        hook=_vec(-c * SCALE for c in HOOK), tip=_vec(CANDLE_TIP), colour=_vec(colour),
        scale=_num(SCALE), bottom=_num(BOTTOM * SCALE), reach=_num(FALL_REACH),
        profile=HIT_PROFILE,
        half_height=_num(HALF_HEIGHT * SCALE),
        sconces=''.join('    self.AddCandle(MakeVector(%s))\n' % _vec(s) for s in SCONCES),
        mesh=_obj(MESH_PATH), candle=_obj(CANDLE_PATH), flame=_obj(FLAME_SYSTEM),
        break_fx=_obj(BREAK_SYSTEM), fire_fx=_obj(FIRE_SYSTEM), drop_sound=_obj(DROP_SOUND),
        land_sound=_obj(LAND_SOUND), fade_mic=_obj(FADE_MIC),
        candle_fade_mic=_obj(CANDLE_FADE_MIC))


def inner(game_path):
    """'/Game/X/Y' -> 'OMD/Content/X/Y'."""
    return 'OMD/Content' + game_path[len('/Game'):]


# -- the kit ---------------------------------------------------------------------------------

def _blueprint():
    """{inner path: bytes} of BP_RiftLordChandelier, compiled with bpkit."""
    from riftlord.core import gamepath
    from riftlord.types import setup
    if setup.unavailable():
        raise SystemExit('the Chandelier needs Setup: %s' % setup.unavailable())
    from bpkit import actor, widget
    from bpkit.typedb import add_package
    folder = gamepath.scratch('chandelier-kit')
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, 'Chandelier.py'), 'w') as fh:
        fh.write(script())
    desc = {'name': CLASS_NAME, 'path': PACKAGE, 'parent': PARENT, 'script': 'Chandelier.py',
            'replicates': True, 'defaults': {'Protodata': {'Guid': GUID.hex()}}, '_dir': folder}
    widget.declare([desc])
    pkg = actor.compile(desc)
    problems = widget.verify(pkg, CLASS_NAME, PACKAGE)
    if problems:
        raise SystemExit('the Chandelier Blueprint did not build: %s' % '; '.join(problems))
    add_package(widget.db(), pkg, PACKAGE)
    ua, ux = pkg.write()
    return {inner(PACKAGE) + '.uasset': ua, inner(PACKAGE) + '.uexp': ux}


def _table():
    """{inner path: bytes} of the TrapProtos table holding ROW."""
    from riftlord.build import missionproto
    from riftlord.build import modtable
    t = missionproto.read('/Game/Data/TrapProtos')
    row = t.clone(TEMPLATE_ROW, ROW)
    t.set_guid(t.field(row, 'Guid'), _guid_text(GUID))
    t.set_soft_path(t.field(row, 'TrapClass'), _obj(PACKAGE) + '_C')
    t.set_text(t.field(row, 'DisplayName'), '', '%s_Name' % MOD_KEY, NAME)
    t.set_text(t.field(row, 'InteractText'), '', '%s_Interact' % MOD_KEY, INTERACT)
    t.set_text(t.field(row, 'Description'), '', '%s_Description' % MOD_KEY, DESCRIPTION)
    t.set_bool(t.field(row, 'bUsesCustomInteractionLogic'), True)
    t.rows = [row]
    modtable.rename_package(t.pkg, '/Game/Data/TrapProtos', 'TrapProtos', TABLE,
                            TABLE.rsplit('/', 1)[-1])
    ua, ux = t.write()
    return {inner(TABLE) + '.uasset': ua, inner(TABLE) + '.uexp': ux}


def _guid_text(raw):
    """The uuid text whose `missionproto.Table.set_guid` bytes are `raw` (four int32)."""
    import uuid
    return str(uuid.UUID(bytes=struct.pack('>4I', *struct.unpack('<4I', raw))))


def _shipped_image(path):
    """The top mip of a shipped texture as an RGBA PIL image."""
    import tempfile
    from riftlord.core import gamefiles
    from riftlord.formats.ue import texture
    stem = '../../../OMD/Content' + path[len('/Game'):]
    with tempfile.TemporaryDirectory(prefix='chandelier-tex-') as tmp:
        paths = []
        for ext in ('.uasset', '.uexp', '.ubulk'):
            if stem + ext not in gamefiles.index():
                paths.append(None)
                continue
            p = os.path.join(tmp, 'tex' + ext)
            with open(p, 'wb') as fh:
                fh.write(gamefiles.read(stem + ext))
            paths.append(p)
        return texture.to_image(texture.parse(*paths)).convert('RGBA')


def _materials():
    """({inner path: bytes}, problems) of the swapped MRA texture and the two fade copies."""
    from PIL import Image
    from riftlord.core import gamepath
    from riftlord.fab import library, micwrite
    orm = _shipped_image(ORM_TEXTURE)
    occlusion, rough, metal, _a = orm.split()
    mra = Image.merge('RGBA', (metal, rough, occlusion, Image.new('L', orm.size, 255)))
    if mra.size != (MRA_SIZE, MRA_SIZE):
        mra = mra.resize((MRA_SIZE, MRA_SIZE), Image.LANCZOS)
    out, problems = {}, []
    tex = micwrite.write_texture('mra', MRA_SIZE, mra, MRA_TEXTURE,
                                 gamepath.scratch('chandelier-textures'))
    problems += ['%s: %s' % (MRA_TEXTURE, p) for p in micwrite.texture_check(tex, 'mra', MRA_SIZE)]
    for ext, data in tex.items():
        out[inner(MRA_TEXTURE) + ext] = data
    for path, textures in ((FADE_MIC, {'BC': BC_TEXTURE, 'MRA': MRA_TEXTURE, 'NM': NM_TEXTURE}),
                           (CANDLE_FADE_MIC, {'BC': CANDLE_BC, 'MRA': DULL_MRA, 'NM': CANDLE_NM})):
        spec = library.mic_spec('alphamask', {'emissive_scale': 0.0}, textures)
        blobs, expect = micwrite.write_mic(spec, path)
        problems += micwrite.readback_problems(blobs, path, expect, KIT_ROOT,
                                               tuple(textures.values()))
        for ext, data in blobs.items():
            out[inner(path) + ext] = data
    return out, problems


_KIT = {}


def kit_key():
    """What the kit is built from: its sources here and the game's packages it copies."""
    from riftlord.core import gamefiles
    h = hashlib.sha1(repr((KIT_VERSION, script(), GUID, TEMPLATE_ROW, NAME, INTERACT,
                           DESCRIPTION, MRA_SIZE)).encode())
    for path in ('/Game/Data/TrapProtos', ORM_TEXTURE):
        stem = '../../../OMD/Content' + path[len('/Game'):]
        h.update(stem.encode() + str(gamefiles.index().get(stem + '.uasset')).encode())
    return h.hexdigest()


def kit():
    """{inner path: bytes} of every package of the kit, built once per process."""
    key = kit_key()
    if key not in _KIT:
        files = {}
        files.update(_blueprint())
        files.update(_table())
        mats, problems = _materials()
        if problems:
            raise SystemExit('the Chandelier materials did not read back: %s'
                             % '; '.join(problems[:3]))
        files.update(mats)
        _KIT.clear()
        _KIT[key] = files
    return dict(_KIT[key])


def kit_entries():
    """Sorted inner paths of the kit (built)."""
    return sorted(kit())


def digest(files):
    """sha1 of the kit entries in `files` ({inner path: bytes}), or None when it holds none:
    what a build carries, compared with digest(kit())."""
    folder = inner(KIT_ROOT) + '/'
    keys = sorted(k for k in files if k.startswith(folder))
    if not keys:
        return None
    h = hashlib.sha1()
    for k in keys:
        h.update(k.encode() + b'\0' + hashlib.sha1(files[k]).digest())
    return h.hexdigest()


# -- the plan ----------------------------------------------------------------------------------

def is_item(item):
    return isinstance(item, dict) and item.get('kind') == 'kit' and item.get('name') == CLASS


def items(plan):
    """[(index in `added`, item)] of the plan's chandeliers."""
    return [(n, a) for n, a in enumerate((plan or {}).get('added') or []) if is_item(a)]


def used(plan):
    return bool(items(plan))


def _position(matrix):
    return matrix[12], matrix[13], matrix[14]


def chain_length(item, plan):
    """uu of chain above the chandelier's hook: from the hook to the chain's first link under
    the post of its group (a Hanging Chain), when that post hangs over the hook; else 0."""
    group = item.get('group')
    if not group or not item.get('matrix'):
        return 0.0
    from riftlord.mapeditor import compounds
    x, y, z = _position(item['matrix'])
    best = 0.0
    for other in (plan or {}).get('added') or []:
        if other is item or other.get('group') != group or not other.get('matrix'):
            continue
        c = compounds.COMPOUNDS.get(other.get('compound') or '')
        if not c or c.get('type') != 'chain' or other.get('name') != c['anchor']['name']:
            continue
        px, py, pz = _position(other['matrix'])
        if math.hypot(px - x, py - y) <= CHAIN_XY_TOLERANCE and pz - CHAIN_IN_POST_Z > z:
            best = max(best, round(pz - CHAIN_IN_POST_Z - z, 2))
    return best


# -- the build ---------------------------------------------------------------------------------

def place(builder, item, rename, plan, report):
    """Write a placed chandelier, retargeted from the catalogue's boom barrel placement, with
    its ChainLength. Returns its package index (kitactors.place transforms it)."""
    from riftlord.build import apply as applier
    from riftlord.mapeditor import kitactors
    got = applier.donor_for(DONOR)
    if not got:
        raise SystemExit('no %s placement to retarget a chandelier from' % DONOR)
    src, j = got
    view, dropped = kitactors._chest_view(src, j, PACKAGE, cls=CLASS,
                                          ancestor=kitactors.CHEST_ANCESTOR)
    actor = builder.lift_actor(view, j, rename=rename)
    applier.reset_component_transforms(builder, actor)
    for i, e in enumerate(builder.pkg.exports):
        if struct.unpack_from('<i', e['meta'], 12)[0] == actor:
            builder.clear_property(i + 1, 'BoxExtent')
    length = chain_length(item, plan)
    if length > 0:
        builder.set_property(actor, 'ChainLength', 'FloatProperty', struct.pack('<f', length))
    rec = report.setdefault('chandelier', {'placed': 0, 'chains': [], 'dropped': dropped})
    rec['placed'] += 1
    rec['chains'].append(length)
    return actor


def add_kit(files, plan, report):
    """`files` with the kit added when the plan places a chandelier."""
    if not used(plan):
        return files
    got = kit()
    files = dict(files)
    files.update(got)
    report.setdefault('chandelier', {}).update({'kit': len(got)})
    return files


# -- the editor's view -------------------------------------------------------------------------

def parts():
    """[{mesh, component, loc, rot, scale}] the Map Editor draws: the chandelier on its hook
    and a candle on each sconce."""
    at = [-c * SCALE for c in HOOK]
    out = [{'mesh': MESH, 'component': 'Body', 'loc': [round(v, 3) for v in at],
            'rot': [0.0, 0.0, 0.0], 'scale': [SCALE] * 3}]
    for n, s in enumerate(SCONCES):
        out.append({'mesh': CANDLE, 'component': 'Candle_%d' % n,
                    'loc': [round(at[i] + s[i] * SCALE, 3) for i in range(3)],
                    'rot': [0.0, 0.0, 0.0], 'scale': [SCALE] * 3})
    return out


# -- measurement -------------------------------------------------------------------------------

def _parts_of(model):
    """[(lo, hi, vertex count)] of a mesh's connected pieces (vertices welded by position)."""
    verts, tris = model['verts'], model['tris']
    weld, key = [], {}
    for p in verts:
        weld.append(key.setdefault(tuple(round(c, 2) for c in p), len(key)))
    up = list(range(len(key)))

    def root(a):
        while up[a] != a:
            up[a] = up[up[a]]
            a = up[a]
        return a
    for i in range(0, len(tris), 3):
        a = root(weld[tris[i]])
        for k in (1, 2):
            up[root(weld[tris[i + k]])] = a
    groups = collections.defaultdict(list)
    for i, p in enumerate(verts):
        groups[root(weld[i])].append(p)
    return [([min(p[i] for p in g) for i in range(3)], [max(p[i] for p in g) for i in range(3)],
             len(g)) for g in groups.values()]


#: A sconce pan: a flat disc this wide (uu) and at most this thick.
PAN_WIDTH = (15.0, 18.0)
PAN_THICKNESS = 3.0


def measure():
    """{hook, bottom, halfHeight, halfWidth, sconces, candleTip, candleWidth} read from the
    meshes, as the constants above record them."""
    from riftlord.formats.omd3 import omd3mesh
    m = omd3mesh.read(MESH)
    verts = m['verts']
    top = max(v[2] for v in verts)
    loop = [v for v in verts if v[2] >= top - 0.01]
    pans = []
    for lo, hi, _n in _parts_of(m):
        w = (hi[0] - lo[0], hi[1] - lo[1])
        if all(PAN_WIDTH[0] <= s <= PAN_WIDTH[1] for s in w) and hi[2] - lo[2] <= PAN_THICKNESS:
            pans.append((round((lo[0] + hi[0]) / 2, 2), round((lo[1] + hi[1]) / 2, 2),
                         round(hi[2], 2)))
    c = omd3mesh.read(CANDLE)
    cv = c['verts']
    ctop = max(v[2] for v in cv)
    band = [v for v in cv if v[2] >= ctop - 0.1 * (ctop - min(p[2] for p in cv))]
    zs = [v[2] for v in verts]
    return {'hook': tuple(round(sum(v[i] for v in loop) / len(loop), 2) for i in range(2))
            + (round(top, 2),),
            'bottom': round(min(zs), 2), 'halfHeight': round((max(zs) - min(zs)) / 2, 2),
            'halfWidth': round(max(max(v[i] for v in verts) - min(v[i] for v in verts)
                                   for i in range(2)) / 2, 2),
            'sconces': sorted(pans),
            'candleTip': (round(sum(v[0] for v in band) / len(band), 2),
                          round(sum(v[1] for v in band) / len(band), 2), round(ctop, 2)),
            'candleWidth': round(sum(max(v[i] for v in cv) - min(v[i] for v in cv)
                                     for i in range(2)) / 2, 2)}


# -- gates ---------------------------------------------------------------------------------------

def _placed(files):
    """[(actor index, ChainLength)] of the chandeliers placed in a built level."""
    from riftlord.mapeditor import kitactors
    return [(actor, vals.get('ChainLength', 0.0))
            for actor, vals in (kitactors.placed(files).get(CLASS) or [])]


def _row_problems(files):
    """[text]: the table holds ROW naming the class with GUID, custom interaction on, and
    the class default object names GUID."""
    from riftlord.build import makemod
    from riftlord.build import missionproto
    from riftlord.formats.ue.uassetio import Package
    out = []
    head = inner(TABLE)
    try:
        rows = makemod._table_rows(files[head + '.uasset'], files[head + '.uexp'])
    except (Exception, SystemExit) as exc:             # noqa: BLE001
        return ['the table does not read: %s' % exc]
    if [n for n, _g in rows] != [ROW]:
        out.append('the table holds %s, not %s' % ([n for n, _g in rows], ROW))
    pkg = Package(files[head + '.uasset'], files[head + '.uexp'])
    pkg.load_payloads()
    t = missionproto.Table(pkg)
    row = t.row(ROW)
    if row:
        names = t.names
        cls = t.field(row, 'TrapClass')
        idx = struct.unpack_from('<i', bytes(cls.payload), 0)[0] if cls else -1
        if not cls or names[idx] != _obj(PACKAGE) + '_C':
            out.append('TrapClass is %s' % (names[idx] if cls and 0 <= idx < len(names) else None))
        guid = t.field(row, 'Guid')
        if not guid or bytes(guid.payload)[-16:] != GUID:
            out.append('the row Guid is not the kit\'s')
        custom = t.field(row, 'bUsesCustomInteractionLogic')
        if not custom or not custom.bool_value:
            out.append('custom interaction is off')
    bp = inner(PACKAGE)
    if bytes(GUID) not in files.get(bp + '.uexp', b''):
        out.append('the class default object does not name the row Guid')
    return out


#: The profiles that shoot: player projectiles, and the hitscan weapon's trace.
SHOT_PROFILES = ('OMDPlayerProjectile', 'OMDProjectile')


def _profiles():
    """{profile: (object type, {channel: response})} and {channel: default response} from
    the game's DefaultEngine.ini (a custom entry without a Response blocks)."""
    import re
    from riftlord.core import gamefiles
    text = gamefiles.read('../../../OMD/Config/DefaultEngine.ini').decode('utf-8', 'replace')
    defaults = {m.group(2): m.group(1) for m in re.finditer(
        r'\+DefaultChannelResponses=\(Channel=\w+,DefaultResponse=(\w+),[^)]*Name="(\w+)"\)', text)}
    out = {}
    for m in re.finditer(r'\+Profiles=\(Name="(\w+)",[^\n]*?ObjectTypeName="(\w*)",'
                         r'CustomResponses=\(([^\n]*?)\)\)', text):
        responses = {c: r or 'ECR_Block' for c, r in re.findall(
            r'\(Channel="(\w+)"(?:,Response=(\w+))?\)', m.group(3))}
        out[m.group(1)] = (m.group(2), responses)
    return out, defaults


def response(profiles, defaults, profile, channel):
    """How `profile` responds to `channel` (engine channels default to Block)."""
    return profiles[profile][1].get(channel) or defaults.get(channel, 'ECR_Block')


def hit_problems(files=None):
    """[text]: a shot does not block on the chandelier's profile both ways, or the Blueprint
    does not bind its parts' hits to the trap base's OnHit."""
    out = []
    try:
        profiles, defaults = _profiles()
        mine = profiles[HIT_PROFILE][0]
        for shot in SHOT_PROFILES:
            theirs = profiles[shot][0]
            a = response(profiles, defaults, shot, mine)
            b = response(profiles, defaults, HIT_PROFILE, theirs)
            if a != 'ECR_Block' or b != 'ECR_Block':
                out.append('%s meets %s with %s / %s' % (shot, HIT_PROFILE, a, b))
    except (KeyError, Exception) as exc:              # noqa: BLE001
        out.append('the collision profiles do not read: %s' % exc)
    head = (files or kit()).get(inner(PACKAGE) + '.uasset', b'')
    for name in (b'OnComponentHit', b'OnHit', HIT_PROFILE.encode()):
        if name not in head:
            out.append('the Blueprint does not name %s' % name.decode())
    return out


def gates(files, plan):
    """[(name, ok, detail)]: the kit is in the pak byte for byte and nothing else is under its
    folder; its imports resolve and each path has one spelling; its row and class agree; each
    chandelier is placed with the chain length its group gives."""
    if not used(plan):
        return []
    from riftlord.core import pakspelling
    from riftlord.mapeditor import probe
    out = []
    want = kit()
    folder = inner(KIT_ROOT) + '/'
    differ = sorted(k for k, v in want.items() if files.get(k) != v)
    extra = sorted(k for k in files if k.startswith(folder) and k not in want)
    out.append(('Chandelier kit carried byte for byte (%d files)' % len(want),
                not differ and not extra,
                '; '.join(['differs or missing: ' + ', '.join(differ[:3])] * bool(differ)
                          + ['not the kit\'s: ' + ', '.join(extra[:3])] * bool(extra))))
    missing, checked, cache = [], 0, {}
    for k in sorted(want):
        if k.endswith('.uasset'):
            n, bad = probe.import_problems(files, k, cache)
            checked += n
            missing += ['%s: %s' % (os.path.basename(k), b) for b in bad]
    out.append(('Chandelier kit: every import names an export (%d)' % checked, not missing,
                '; '.join(missing[:3])))
    from riftlord.build import leveltitle
    level = [k for k in files if leveltitle.is_level(k)]
    pool = {k: files[k] for k in list(want) + level if k in files}
    two = sorted(p for p, forms in pakspelling.spellings(pool).items() if len(forms) > 1)
    out.append(('Chandelier kit and level: every package path under one FName spelling',
                not two, ', '.join(two[:3])))
    bad = _row_problems(files)
    out.append(('Chandelier row %s names %s, its Guid and custom interaction' % (ROW, CLASS),
                not bad, '; '.join(bad)))
    bad = hit_problems(files)
    out.append(('Chandelier shots: %s blocks player projectiles and the hitscan trace both '
                'ways; body and chain box hits go to OnHit' % HIT_PROFILE, not bad,
                '; '.join(bad)))
    placed = _placed(files)
    expect = sorted(chain_length(item, plan) for _n, item in items(plan))
    got = sorted(round(v, 2) for _a, v in placed)
    out.append(('Chandeliers placed with their chain lengths (%s)'
                % ', '.join('%g' % v for v in expect),
                len(placed) == len(expect) and all(abs(a - b) < 0.01 for a, b in zip(got, expect)),
                'placed %s' % got))
    return out


# -- offline check ---------------------------------------------------------------------------

#: The level `--check` builds on, and the shipped boom barrels there the check hangs
#: chandeliers beside: (x, y, hook z, post z) of two barrels whose chains have posts.
CHECK_LEVEL = 'Scenario_08'
CHECK_SPOTS = ((6450.0, 930.0, 1110.0, 1485.0), (6190.0, 3220.0, 660.0, 970.0))
#: The check's chandeliers hang this far along +y from each barrel.
CHECK_OFFSET = 300.0


def hanging(at, post_z, links=None, group='chandelier'):
    """Plan items of a Chandelier Trap compound with the chandelier's hook at `at` (x, y, z),
    as compounds.js places one: the post, ring, chain segments and the chandelier, one
    group. `links` defaults to the most whose post stays at or under `post_z`."""
    from riftlord.mapeditor import compounds
    if links is None:
        links = 1
        for n in range(1, compounds.CHAIN_LINKS_MAX + 1):
            if _hook_drop(n) + CHAIN_IN_POST_Z <= post_z - at[2]:
                links = n
    params = {'links': links, 'item': COMPOUND}
    layout = compounds.layout(COMPOUND, params)
    hook = next(m for name, _k, m in layout if name == CLASS)
    shift = [at[i] - hook[i][3] for i in range(3)]
    out = []
    for k, (name, kind, m) in enumerate(layout):
        mat = [m[0][0], m[1][0], m[2][0], 0.0, m[0][1], m[1][1], m[2][1], 0.0,
               m[0][2], m[1][2], m[2][2], 0.0,
               m[0][3] + shift[0], m[1][3] + shift[1], m[2][3] + shift[2], 1.0]
        entry = {'name': name, 'kind': kind, 'matrix': [round(v, 4) for v in mat],
                 'group': group}
        if k == 0:
            entry.update({'compound': COMPOUND, 'params': params})
        out.append(entry)
    return out


def _hook_drop(links):
    """uu from the chain's first link (under the post) down to the hook for `links` links."""
    from riftlord.mapeditor import compounds
    segs, left = [], links
    for n, _mesh, drop in compounds.SEGMENTS:
        while left >= n:
            segs.append((n, drop))
            left -= n
    total = 0.0
    for i, (n, drop) in enumerate(segs):
        total += (compounds.STACK_BELOW_X6 if n == 6 else drop) if i < len(segs) - 1 else drop
    return total


def check_plan():
    """A plan hanging a chandelier beside each CHECK_SPOTS barrel."""
    added = []
    for k, (x, y, z, post) in enumerate(CHECK_SPOTS):
        added += hanging((x, y + CHECK_OFFSET, z), post, group='chandelier-%d' % k)
    return {'level': CHECK_LEVEL, 'added': added}


def check(out_dir, log=print):
    """Build the kit, place the check plan into a copy of CHECK_LEVEL and run the gates, the
    trailer gate, pkgcheck and the registry rules. Returns failures."""
    from riftlord.build import apply as applier
    from riftlord.build import makemod
    from riftlord.build import trailers
    from riftlord.build.navigation import navpatch
    from riftlord.core import pkgcheck
    from riftlord.mapeditor import kitactors
    from riftlord.mapeditor import probe
    fails = []
    plan = check_plan()
    stem = 'OMD/Content/Mods/ChandelierCheck/Maps/ChandelierCheck_Map01'
    got = navpatch.fetch_map(navpatch.map_path(CHECK_LEVEL), out_dir)
    with open(got['.umap'], 'rb') as fh:
        ua = fh.read()
    with open(got['.uexp'], 'rb') as fh:
        ux = fh.read()
    ua, ux, report = applier.apply_plan(ua, ux, CHECK_LEVEL,
                                        '/Game/Mods/ChandelierCheck/Maps/ChandelierCheck_Map01',
                                        'ChandelierCheck_Map01', plan=plan)
    for line in report['skipped']:
        log('  skipped: %s' % line)
        fails.append('skipped: %s' % line)
    files = add_kit({stem + '.umap': ua, stem + '.uexp': ux}, plan, report)
    log('  placed: %s' % json.dumps(report.get('chandelier')))
    for name in sorted(files):
        base, ext = os.path.splitext(name)
        if ext in ('.umap', '.uasset') and base + '.uexp' in files:
            bad = pkgcheck.problems(files[name], files[base + '.uexp'], os.path.basename(base))
            fails += ['pkgcheck %s: %s' % (os.path.basename(name), b) for b in bad]
    log('%s  pkgcheck on the level and the kit' % ('FAIL' if any(f.startswith('pkgcheck')
                                                                  for f in fails) else 'PASS'))
    reg = makemod.registry_problems(files, installed=False)
    log('%s  registry rules%s' % ('PASS' if not reg else 'FAIL', (': ' + '; '.join(reg[:3])) if reg else ''))
    fails += ['registry: %s' % r for r in reg]
    checked, missing = probe.import_problems(files, stem + '.umap')
    log('%s  the level: every import names an export (%d)%s'
        % ('PASS' if not missing else 'FAIL', checked, (': ' + '; '.join(missing[:3])) if missing else ''))
    fails += ['level import: %s' % m for m in missing]
    for name, ok, detail in (gates(files, plan) + kitactors.gates(files, plan)
                             + trailers.gates(files, plan)):
        log('%s  %s%s' % ('PASS' if ok else 'FAIL', name, (': ' + detail) if detail else ''))
        if not ok:
            fails.append(name)
    for name, data in files.items():
        path = os.path.join(out_dir, 'files', name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as fh:
            fh.write(data)
    return fails


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--check', metavar='DIR', help='build the kit and place chandelier chains '
                    'into a copy of %s offline' % CHECK_LEVEL)
    ap.add_argument('--measure', action='store_true', help='print what the meshes measure')
    args = ap.parse_args(argv)
    if args.measure:
        print(json.dumps(measure(), indent=1))
    if args.check:
        os.makedirs(args.check, exist_ok=True)
        fails = check(args.check)
        print('%d failure(s)' % len(fails))
        return 1 if fails else 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
