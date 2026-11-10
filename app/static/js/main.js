// Os links permanecem visíveis se JavaScript estiver desativado.
document.querySelectorAll('.account-thumbnail img').forEach((image) => {
    const showPlaceholder = () => {
        image.hidden = true;
        image.parentElement.querySelector('.account-thumbnail-placeholder').removeAttribute('aria-hidden');
    };
    image.addEventListener('error', showPlaceholder);
    if (image.complete && image.naturalWidth === 0) showPlaceholder();
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
