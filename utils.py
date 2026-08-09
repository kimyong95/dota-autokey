def updated_abilities(curr_abilities, prev_abilities, filter_info):
    """Current info of the abilities whose `filter_info` field changed this tick.

    GSI's `previously.abilities` lists only the fields that changed, so a slot
    appearing there with `filter_info` in it is one that just ticked. The value
    returned is the *current* info for that slot, keyed by slot.
    """
    if not isinstance(prev_abilities, dict):    # GSI sends `false` when the block is new
        return {}
    return {
        prev_ability_slot: curr_abilities[prev_ability_slot]
        for prev_ability_slot, prev_ability_info in prev_abilities.items()
        if filter_info in prev_ability_info
    }
