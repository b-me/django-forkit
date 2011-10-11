from copy import deepcopy
from django.db import models
from forkit import utils, signals
from forkit.commit import commit_model_object

def _reset_one2one(reference, instance, refvalue, field, direct, accessor, deep, memo):
    value = utils._get_field_value(instance, accessor)[0]
    # only if an instance value exists and it's a deep reset
    if refvalue and value and deep:
        _memoize_reset(refvalue, value, deep=deep, commit=False, memo=memo)
        instance._forkstate.defer_commit(accessor, value, direct=direct)

def _reset_foreignkey(reference, instance, refvalue, field, direct, accessor, deep, memo):
    value = utils._get_field_value(instance, accessor)[0]
    # direct foreign keys used as is (shallow) or forked (deep)
    if refvalue and value and deep:
        _memoize_reset(refvalue, value, deep=deep, commit=False, memo=memo)
    elif not value:
        value = refvalue

    instance._forkstate.defer_commit(accessor, value, direct=direct)

def _reset_field(reference, instance, accessor, deep, memo):
    """Creates a copy of the reference value for the defined ``accessor``
    (field). For deep forks, each related object is related objects must
    be created first prior to being recursed.
    """
    value, field, direct, m2m = utils._get_field_value(reference, accessor)

    # explicitly block reverse and m2m relationships..
    if not direct or m2m:
        return

    if isinstance(field, models.OneToOneField):
        return _reset_one2one(reference, instance, value, field, direct,
            accessor, deep, memo)

    if isinstance(field, models.ForeignKey):
        return _reset_foreignkey(reference, instance, value, field, direct,
            accessor, deep, memo)

    # non-relational field, perform a deepcopy to ensure no mutable nonsense
    setattr(instance, accessor, deepcopy(value))

def _memoize_reset(reference, instance, **kwargs):
    "Resets the specified instance relative to ``reference``"
    # popped so it does not get included in the config for the signal
    memo = kwargs.pop('memo', None)

    # for every call, keep track of the reference and the object (fork).
    # this is used for recursive calls to related objects. this ensures
    # relationships that follow back up the tree are caught and are merely
    # referenced rather than traversed again.
    if memo is None:
        memo = utils.Memo()
    elif memo.has(reference):
        return memo.get(reference)

    if not isinstance(instance, reference.__class__):
        raise TypeError('The instance supplied must be of the same type as the reference')

    memo.add(reference, instance)

    # default configuration
    config = {
        'fields': None,
        'exclude': ['pk'],
        'deep': False,
        'commit': True,
    }

    # update with user-defined
    config.update(kwargs)

    # pre-signal
    signals.pre_reset.send(sender=reference.__class__, reference=reference,
        instance=instance, config=kwargs)

    fields = config['fields']
    exclude = config['exclude']
    deep = config['deep']
    commit = config['commit']

    # no fields are defined, so get the default ones for shallow or deep
    if not fields:
        fields = utils._default_model_fields(reference, exclude=exclude, deep=deep)

    if not hasattr(instance, '_forkstate'):
        # for the duration of the reset, each object's state is tracked via
        # the a ForkState object. this is primarily necessary to track
        # deferred commits of related objects
        instance._forkstate = utils.ForkState(reference=reference)

    elif instance._forkstate.has_deferreds:
        instance._forkstate.clear_commits()

    # iterate over each field and fork it!. nested calls will not commit,
    # until the recursion has finished
    for accessor in fields:
        _reset_field(reference, instance, accessor, deep=deep, memo=memo)

    # post-signal
    signals.post_reset.send(sender=reference.__class__, reference=reference,
        instance=instance)

    if commit:
        commit_model_object(instance)

    return instance


def reset_model_object(reference, instance, **kwargs):
    "Resets the ``instance`` object relative to ``reference``'s state."
    return _memoize_reset(reference, instance, **kwargs)