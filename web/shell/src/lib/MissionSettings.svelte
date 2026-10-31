<script>
  // One mission's settings, changed without opening an editor: what OrcsMustUI shows
  // for it, its Guid, its level's Working Title, its pictures, the campaign that lists it
  // and its place there (Endless lists it alone), whether Endless has it too, and the
  // heroes and items beating it unlocks. The dialog is the one all of Rift Lord's pages share
  // (web/shared/MissionDetails.svelte); its campaign's own settings save at once.
  import MissionDetails from '../../../shared/MissionDetails.svelte'
  import { sendable } from '../../../shared/LoadPicture.svelte'
  import { missionSettings, saveMissionSettings, checkGuid, newGuid, saveCampaign } from './api.js'

  let { id, onclose, onsaved } = $props()

  let s = $state(null)
  let error = $state('')
  let saving = $state(false)
  //: the Guid field's text and whether its last check passed (guidTools)
  let guid = ''
  let guidOk = true

  $effect(() => {
    missionSettings(id).then((got) => { s = got; guid = got.guid || '' })
      .catch((e) => { error = e.message })
  })

  /** The dialog's Guid field: checked against the shipped missions and Missions. */
  function guidTools() {
    return {
      own: s.guid || '',
      value: s.guid || '',
      check: async (text) => {
        guid = text
        if (!text) { guidOk = true; return null }
        const got = await checkGuid(text, id)
        guidOk = !!got.ok
        return got
      },
      generate: async () => (await newGuid()).guid,
    }
  }

  async function save(v) {
    if (v.placement === 'new' && !v.newCampaign.name.trim()) { error = 'Name the new campaign.'; return }
    if (!guidOk) { error = 'Fix the Guid first.'; return }
    saving = true
    error = ''
    try {
      await saveMissionSettings({
        id, placement: v.placement, endless: v.endless,
        ...(s.canEndlessOnly ? { endlessOnly: !!v.endlessOnly } : {}),
        ...('resetDoors' in v ? { resetDoors: !!v.resetDoors } : {}),
        ...(s.scrambleTiers ? { scramble: v.scramble } : {}),
        ...(s.unlockChoices && v.unlocks ? { unlocks: v.unlocks } : {}),
        ...(guid && guid.toUpperCase() !== (s.guid || '') ? { guid } : {}),
        ...(v.campaignIndex != null ? { campaignIndex: v.campaignIndex } : {}),
        ...(s.workingTitle && v.workingTitle !== s.workingTitle.value
          ? { workingTitle: v.workingTitle } : {}),
        details: { author: v.author, version: v.version, description: v.description,
                   hub: v.hub,
                   ...(s.canPicture ? { picture: sendable(v.picture),
                                        portalPicture: sendable(v.portalPicture) } : {}) },
        newCampaign: v.placement === 'new' ? v.newCampaign : null,
      })
      onsaved?.()
      onclose()
    } catch (e) {
      error = e.message
    } finally {
      saving = false
    }
  }
</script>

{#if s}
  <MissionDetails heading="{s.title} Settings" campaigns={s.choices}
                  values={{ ...s.details, placement: s.placement, endless: s.endless,
                            endlessOnly: !!s.endlessOnly,
                            ...(s.canResetDoors ? { resetDoors: s.resetDoors } : {}),
                            picture: s.picture, portalPicture: s.portalPicture,
                            scramble: s.scramble, unlocks: s.unlocks }}
                  scrambleTiers={s.scrambleTiers}
                  unlockChoices={s.unlockChoices}
                  allowPicture={!!s.canPicture} title={s.title}
                  sourcePictures={s.sourcePictures}
                  guidTools={s.guid ? guidTools() : null}
                  workingTitle={s.workingTitle}
                  allowEndlessVariant={!!s.canEndlessOnly}
                  campaignTools={{ save: saveCampaign }}
                  shippedLocked={s.shipped} allowShipped={!!s.editable}
                  {onclose} onsave={save} {saving} {error} />
{:else if error}
  <MissionDetails heading="Mission Settings" values={{}} {onclose} {error} />
{/if}
