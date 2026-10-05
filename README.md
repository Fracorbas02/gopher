# gopher — le gopherhole de Bastien BONORA

Contenu servi par [Gophernicus](https://github.com/gophernicus/gophernicus)
(docker sur le VPS) : un mix du portfolio
([`../portfolio`](../portfolio)) et de la doc perso
([`../Fracorbas-Docs`](../Fracorbas-Docs)), version old-school.

## Arborescence

```
gophermap                      menu racine (bannière + sections)
presentation/                  parcours, CV, certifications, PGP
projets/                       Nastruire, Bastodoc, ce gopherhole
ctf/                           write-ups TryHackMe (HTML portfolio + doc)
docs/                          miroir de Fracorbas-Docs/docs (md -> txt)
blog/                          articles de Fracorbas-Docs/blog (md -> txt)
tools/build.py                 générateur (non déployé sur le VPS)
```

Tous les répertoires contiennent un `gophermap` (menu) et les contenus
sont des fichiers texte, PDF ou images. Les images de chaque article/doc
sont dans `images/` à côté des fichiers qui les référencent.

## Régénérer le contenu

Le contenu de ce dépôt est **généré** depuis les deux dépôts voisins.
Après toute modification du portfolio ou de la doc :

```sh
python3 tools/build.py
```

Puis commit + push : le workflow GitHub rsync tout (sauf `tools/`,
`.github/`, `README.md`) vers le VPS, où le conteneur Gophernicus sert
le dossier en direct.

## Rappels Gophernicus

- Une gophermap remplace totalement le listing auto du répertoire
  (y compris le lien parent) → liens « Retour » explicites partout.
- Ligne sans tabulation = texte d'information ; ligne à tabulations
  = lien `type nom <TAB> sélecteur <TAB> hôte <TAB> port`.
- `!Titre` en première ligne = titre du menu.
- Caractères spéciaux en début de ligne d'info (`= - * . : ~ % # !`) =
  directives gophernicus → le générateur les préfixe d'une espace.
- Charset de sortie par défaut : UTF-8 (les accents passent).
- Les fichiers et répertoires doivent être world-readable (le rsync
  du CI force `Fgo=r, Dgo=rx`).

## Tester en local

```sh
# mode inetd : une requête par invocation
printf '/\r\n' | gophernicus -r "$PWD" -p 7070 -h localhost
```
