<script>
  // The mission's details and where it is listed, in the dialog all of Rift Lord's
  // pages share (web/shared/MissionDetails.svelte), kept in step with the spec. A
  // mission in Missions also shows its Guid, which can be set by hand or generated, and
  // takes the same pictures as in the Map Editor and Missions (applied on Save Mission).
  import MissionDetails from '../../../shared/MissionDetails.svelte'
  import { sendable } from '../../../shared/LoadPicture.svelte'
  import { missionGuid, newGuid, setMissionGuid, checkGuid, sourcePictures, saveCampaign,
           scrambleTiers, listCampaigns } from './api.js'

  // pictures: {picture, portalPicture} as the dialog shows them (null for none);
  // onpictures(slot, value) hears a change, which the next save sends
  let { spec, campaigns, onclose, mission = null, map = null, pictures = {},
        allowPicture = false, onpictures = () => {} } = $props()

  // svelte-ignore state_referenced_locally
  const values = { ...$state.snapshot(spec.details), placement: spec.intoCampaign,
                   endless: spec.endless, endlessOnly: !!spec.endlessOnly,
                   resetDoors: !!spec.resetDoors,
                   picture: pictures.picture ?? null, portalPicture: pictures.portalPicture ?? null,
                   scramble: spec.scramble ? { on: true, ...spec.scramble }
                     : { on: false, tiers: [], rows: {} } }
  const same = (a, b) => JSON.stringify(sendable(a) ?? null) === JSON.stringify(sendable(b) ?? null)

  function onchange(v) {
    spec.details = { author: v.author, version: v.version, description: v.description,
                     hub: v.hub, newCampaign: v.newCampaign, campaignIndex: v.campaignIndex }
    spec.intoCampaign = v.placement
    spec.endless = v.endless
    //: a Map Editor mission placed in Endless (Campaign Endless, bucket._endless_variant)
    if (v.endlessOnly || spec.endlessOnly) spec.endlessOnly = !!v.endlessOnly
    spec.resetDoors = !!v.resetDoors
    if (tiers) {
      const { on, ...kept } = v.scramble
      spec.scramble = on ? kept : null
    }
    for (const slot of ['picture', 'portalPicture']) {
      if (!same(v[slot], values[slot])) onpictures(slot, v[slot])
    }
  }

  //: the Guid as saved
  let saved = $state('')
  let ready = $state(!mission)
  //: the source level's own pictures (sourcepictures.py)
  let source = $state(null)
  //: the live Scramble tiers (missionscramble.view), or null until read
  let tiers = $state(null)

  $effect(() => { scrambleTiers().then((got) => { tiers = got }).catch(() => {}) })
  //: the campaigns with their Campaign Index ranges for this mission (bucket.choices_for)
  let picks = $state(null)
  $effect(() => {
    if (!mission) return
    listCampaigns(mission).then((got) => { picks = got }).catch(() => { picks = campaigns })
  })

  $effect(() => {
    if (!mission) return
    missionGuid(mission).then((got) => { saved = got.guid })
      .catch(() => {}).finally(() => { ready = true })
  })
  $effect(() => {
    if (!allowPicture) return
    sourcePictures(map, mission).then((got) => { source = got }).catch(() => {})
  })

  /** The dialog's Guid field. A Guid that checks out is given to the mission at once,
   * as Missions keeps it; the next save builds with it. */
  function guidTools() {
    return {
      own: saved,
      value: saved,
      check: async (text) => {
        if (!text || text.toUpperCase() === saved) return null
        const got = await checkGuid(text, mission)
        if (got.ok) {
          const set = await setMissionGuid(mission, got.guid)
          saved = set.guid
        }
        return got
      },
      generate: async () => (await newGuid()).guid,
    }
  }
</script>

{#if ready && (!mission || picks)}
  <MissionDetails {values} campaigns={picks ?? campaigns} {onclose} {onchange} {allowPicture}
                  campaignTools={{ save: saveCampaign }}
                  title={spec.title} sourcePictures={source} scrambleTiers={tiers}
                  allowEndlessVariant={!!(mission && spec.bundle)}
                  guidTools={mission && saved ? guidTools() : null}>
    {#if !mission || !saved}
      <h4 class="g-head">Mission Guid</h4>
      <p class="g-hint">Generated when the mission is first saved; it can be changed after that.</p>
    {/if}
  </MissionDetails>
{/if}

<style>
  .g-head { font-size: 12px; margin: 16px 0 4px; text-transform: uppercase;
            letter-spacing: 0.06em; color: var(--md-dim); font-weight: 600; }
  .g-hint { font-size: 12px; color: var(--md-dim); margin: 4px 0; }
</style>
