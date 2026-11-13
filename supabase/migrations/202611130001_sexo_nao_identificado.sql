-- Permite informar animais cujo sexo ainda não foi identificado.
-- Preserva os registros, a obrigatoriedade do campo e as permissões existentes.
begin;

alter table public.animais drop constraint animais_sexo_check;
alter table public.animais add constraint animais_sexo_check
    check (sexo in ('Macho', 'Fêmea', 'Não identificado'));

commit;
