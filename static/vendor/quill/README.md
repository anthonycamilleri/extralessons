# Quill 2.0.3 (vendored)

The rich-text editor behind the announcement composers (see
`static/js/richtext.js` and `apps.notifications.forms.RichTextWidget`).

- `quill.js` and `quill.snow.css` are `package/dist/quill.js` and
  `package/dist/quill.snow.css` from the npm tarball
  `https://registry.npmjs.org/quill/-/quill-2.0.3.tgz`, with one change:
  the trailing `sourceMappingURL` comment is removed from each. The source
  maps are not vendored, and `collectstatic` under the production manifest
  storage refuses a file that points at a map it cannot find.
- Licence: BSD-3-Clause (`LICENSE`, from the same tarball).

To upgrade, fetch the new tarball, copy the same two files, strip the
`sourceMappingURL` comments again, and update the version here. There is no build step: the files are served by WhiteNoise as
they are, like `static/js/htmx.min.js`.
