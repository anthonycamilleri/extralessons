/* The announcement editor: a Quill toolbar over a hidden textarea.
 *
 * Any <textarea data-richtext> becomes an editor. The textarea keeps the HTML
 * and is what the form posts; the editor writes back into it as you type and
 * once more on submit. Pictures never travel inside the message: the image
 * button (and a paste or drop) uploads the file to data-upload-url and embeds
 * the URL the server answers with, so the email links to one hosted copy.
 *
 * The formats offered here are exactly the ones the server keeps
 * (apps.notifications.richtext.ALLOWED_TAGS); anything else pasted in is
 * removed before the message is stored, so what you see is what is sent.
 */
(function () {
  "use strict";

  var IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];

  function csrfToken(form) {
    var input = form && form.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input) return input.value;
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function showError(holder, text) {
    var wrapper = holder.parentNode;
    var note = wrapper.querySelector(".richtext-error");
    if (!note) {
      note = document.createElement("p");
      note.className = "richtext-error";
      note.setAttribute("role", "alert");
      wrapper.appendChild(note);
    }
    note.textContent = text;
    note.hidden = !text;
  }

  function upload(url, form, file) {
    var body = new FormData();
    body.append("image", file, file.name || "image");
    return fetch(url, {
      method: "POST",
      body: body,
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrfToken(form) },
    }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return {};
        })
        .then(function (data) {
          if (!response.ok || !data.url) {
            throw new Error(data.error || "The picture could not be uploaded.");
          }
          return data.url;
        });
    });
  }

  function mount(textarea) {
    var form = textarea.form;
    var uploadUrl = textarea.getAttribute("data-upload-url");
    // Toolbar and editor share one block: Quill puts the toolbar before the
    // editor as a sibling, and the admin's field wrapper is a flex row that
    // would otherwise lay them out side by side.
    var wrapper = document.createElement("div");
    wrapper.className = "richtext-field";
    var holder = document.createElement("div");
    holder.className = "richtext-editor";
    wrapper.appendChild(holder);
    textarea.parentNode.insertBefore(wrapper, textarea.nextSibling);
    textarea.hidden = true;

    var quill = new Quill(holder, {
      theme: "snow",
      placeholder: "Write your message…",
      formats: ["bold", "italic", "underline", "header", "list", "link", "image"],
      modules: {
        toolbar: {
          container: [
            ["bold", "italic", "underline"],
            [{ header: [2, 3, false] }],
            [{ list: "ordered" }, { list: "bullet" }],
            ["link", "image"],
            ["clean"],
          ],
          handlers: {
            image: function () {
              var input = document.createElement("input");
              input.type = "file";
              input.accept = IMAGE_TYPES.join(",");
              input.addEventListener("change", function () {
                if (input.files.length) {
                  var range = quill.getSelection(true);
                  quill.uploader.upload(range, input.files);
                }
              });
              input.click();
            },
          },
        },
        uploader: {
          mimetypes: IMAGE_TYPES,
          handler: function (range, files) {
            showError(holder, "");
            var index = range.index;
            Array.prototype.forEach.call(files, function (file) {
              upload(uploadUrl, form, file)
                .then(function (url) {
                  quill.insertEmbed(index, "image", url, "user");
                  index += 1;
                  quill.setSelection(index, 0, "silent");
                  sync();
                })
                .catch(function (error) {
                  showError(holder, error.message);
                });
            });
          },
        },
      },
    });

    // Redisplay after a validation error: the textarea holds what was posted.
    if (textarea.value.trim()) {
      quill.setContents(quill.clipboard.convert({ html: textarea.value }), "silent");
    }

    function sync() {
      // getSemanticHTML gives real <ul>/<ol>; the editor's own DOM marks
      // bullets with data attributes the server would not understand.
      var empty = quill.getText().trim() === "" && !holder.querySelector("img");
      textarea.value = empty ? "" : quill.getSemanticHTML();
    }

    quill.on("text-change", sync);
    if (form) form.addEventListener("submit", sync);
    sync();
  }

  function init() {
    if (typeof Quill === "undefined") return; // the script did not load; the textarea still works
    document.querySelectorAll("textarea[data-richtext]").forEach(mount);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
