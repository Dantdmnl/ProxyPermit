'use strict';

function confirmDiscard(dirty) {
    return !dirty || window.confirm(I18n.t('Niet-opgeslagen invoer verwerpen? Kies Annuleren om verder te werken en op te slaan.'));
}

function protectDialogDismiss(dialog, buttons, isDirty, isBusy) {
    function dismiss() {
        if (!isBusy() && confirmDiscard(isDirty())) dialog.close();
    }
    let backdropPress = false;
    const outside = event => {
        const { left, right, top, bottom } = dialog.getBoundingClientRect();
        return event.target === dialog && (event.clientX < left || event.clientX > right ||
            event.clientY < top || event.clientY > bottom);
    };
    dialog.addEventListener('pointerdown', event => { backdropPress = outside(event); });
    dialog.addEventListener('click', event => {
        if (backdropPress && outside(event)) dismiss();
        backdropPress = false;
    });
    dialog.addEventListener('cancel', event => { event.preventDefault(); dismiss(); });
    buttons.forEach(button => button.addEventListener('click', dismiss));
}
