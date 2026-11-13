// Os links permanecem visíveis se JavaScript estiver desativado.
const announcementErrors = document.querySelector('.announcement-errors');
if (announcementErrors) announcementErrors.focus();

const speciesSelect = document.querySelector('.announcement-form #especie');
const otherSpecies = document.querySelector('.announcement-form #especie_outro');

if (speciesSelect && otherSpecies) {
    const updateOtherSpecies = () => {
        const isOther = speciesSelect.value === 'Outro animal doméstico';
        otherSpecies.closest('.registration-field').hidden = !isOther;
        otherSpecies.disabled = !isOther;
        otherSpecies.required = isOther;
        if (!isOther) {
            otherSpecies.value = '';
            otherSpecies.setAttribute('aria-invalid', 'false');
            delete otherSpecies.dataset.validationTouched;
        }
    };
    speciesSelect.addEventListener('change', updateOtherSpecies);
    updateOtherSpecies();
}

const announcementForm = document.querySelector('.announcement-form');
if (announcementForm) {
    const contactEmail = announcementForm.querySelector('#email_contato');
    const contactPhone = announcementForm.querySelector('#whatsapp');
    const updateFieldValidity = (field) => {
        if (!field.matches('input:not([type="hidden"]), select, textarea')) return;
        const emptyRequired = field.required && field.value.trim() === '';
        const missingContact = announcementForm.dataset.contatoAusente === 'true' &&
            (field === contactEmail || field === contactPhone) &&
            !contactEmail.value.trim() && !contactPhone.value.trim();
        const invalid = !field.disabled && (emptyRequired || missingContact || !field.validity.valid);
        field.setAttribute('aria-invalid', String(invalid));
    };
    announcementForm.addEventListener('focusout', (event) => {
        event.target.dataset.validationTouched = 'true';
        updateFieldValidity(event.target);
    });
    const updateAllFields = () => {
        announcementForm.querySelectorAll('input:not([type="hidden"]), select, textarea').forEach((field) => {
            field.dataset.validationTouched = 'true';
            updateFieldValidity(field);
        });
    };
    announcementForm.addEventListener('invalid', updateAllFields, true);
    announcementForm.addEventListener('submit', updateAllFields);
    ['input', 'change'].forEach((type) => {
        announcementForm.addEventListener(type, (event) => {
            const field = event.target;
            if (field.dataset.validationTouched || field.getAttribute('aria-invalid') === 'true') {
                updateFieldValidity(field);
            }
            if (announcementForm.dataset.contatoAusente === 'true' &&
                (field === contactEmail || field === contactPhone)) {
                updateFieldValidity(contactEmail);
                updateFieldValidity(contactPhone);
            }
        });
    });
}

document.querySelectorAll('.account-thumbnail img').forEach((image) => {
    const showPlaceholder = () => {
        image.hidden = true;
        image.parentElement.querySelector('.account-thumbnail-placeholder').removeAttribute('aria-hidden');
    };
    image.addEventListener('error', showPlaceholder);
    if (image.complete && image.naturalWidth === 0) showPlaceholder();
});

// Sem suporte a dialog, a confirmação continua disponível no próprio menu.
document.querySelectorAll('.account-more .account-confirmation').forEach((confirmation) => {
    const menu = confirmation.closest('.account-more');
    const trigger = confirmation.querySelector('summary');
    const form = confirmation.querySelector('form');
    const dialog = document.createElement('dialog');
    if (typeof dialog.showModal !== 'function') return;

    dialog.className = 'account-confirmation-dialog';
    dialog.setAttribute('aria-labelledby', form.querySelector('h3').id);
    dialog.setAttribute('aria-describedby', form.querySelector('p').id);
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'button account-button-secondary';
    cancel.textContent = 'Cancelar';
    cancel.autofocus = true;
    form.querySelector('.account-confirmation-buttons').prepend(cancel);
    dialog.append(form);
    menu.closest('.account-page').append(dialog);
    menu.classList.add('is-enhanced');

    trigger.addEventListener('click', (event) => {
        event.preventDefault();
        form.querySelector('input[type="checkbox"]').checked = false;
        dialog.showModal();
    });
    cancel.addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => trigger.focus());
});

document.addEventListener('click', (event) => {
    if (event.target.closest('.account-more, .account-confirmation-dialog')) return;
    document.querySelectorAll('.account-more[open]').forEach((menu) => { menu.open = false; });
});
document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || document.querySelector('.account-confirmation-dialog[open]')) return;
    document.querySelectorAll('.account-more[open]').forEach((menu) => {
        menu.open = false;
        menu.querySelector('summary').focus();
    });
});

const menuButton = document.querySelector('.menu-toggle');
const navigation = document.querySelector('#navegacao');
const mobileViewport = window.matchMedia('(max-width: 640px)');

if (menuButton && navigation) {
    const closeMenu = () => {
        menuButton.setAttribute('aria-expanded', 'false');
        navigation.classList.remove('is-open');
    };

    menuButton.hidden = false;
    navigation.classList.add('is-enhanced');

    menuButton.addEventListener('click', () => {
        const isOpen = menuButton.getAttribute('aria-expanded') === 'true';
        menuButton.setAttribute('aria-expanded', String(!isOpen));
        navigation.classList.toggle('is-open', !isOpen);
    });

    navigation.addEventListener('click', (event) => {
        if (event.target.closest('a')) closeMenu();
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && menuButton.getAttribute('aria-expanded') === 'true') {
            closeMenu();
            menuButton.focus();
        }
    });

    mobileViewport.addEventListener('change', closeMenu);
}
