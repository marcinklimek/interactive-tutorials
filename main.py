# -*- coding: utf-8 -*-
import json
import re
import markdown
import os
import html
import urllib.request, urllib.parse, urllib.error
import time
import functools
import logging
import binascii
import datetime

from flask import Flask, render_template, request, make_response, session, Response


from ideone import Ideone

import constants

courses = json.load(open("courses.json"))

# Flask app
app = Flask(__name__)
app.secret_key = constants.SECRET_KEY

sections = re.compile(r"Tutorial\n[=\-]+\n+(.*)\n*Tutorial Code\n[=\-]+\n+(.*)\n*Expected Output\n[=\-]+\n+(.*)\n*Solution\n[=\-]+\n*(.*)\n*", re.MULTILINE | re.DOTALL)
WIKI_WORD_PATTERN = re.compile(r'\[\[([^]|]+\|)?([^]]+)\]\]')
CODING_FOR_KIDS_TITLES = []

current_domain = os.environ.get("DEFAULT_DOMAIN", constants.LEARNPYTHON_DOMAIN)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-d",
        "--domain",
        help="Default domain when running in development mode",
        default=current_domain,
        choices=list(constants.DOMAIN_DATA.keys())
    )

    parser.add_argument("-p", "--port", help="port to listen to", default=5000, type=int)
    parser.add_argument("-H", "--host", help="host", default='127.0.0.1')

    args = parser.parse_args()
    current_domain = args.domain

LANGUAGES = {
    "en": "English",
    "pl": "Polski",
    "fa": "فارسی",
    "es": "Español",
    "it": "Italiano",
    "de": "Deutsch",
    "cn": "普通话",
    "fr": "Français",
    "pt": "Português",
    "tr": "Türkçe",
    "nl": "Nederlands",
}

tutorial_data = {}


def run_code(code, language_id):
    ideone_api = Ideone(
        os.environ['IDEONE_USERNAME'],
        os.environ['IDEONE_PASSWORD'],
        api_url='http://ronreiter.compilers.sphere-engine.com/api/1/service.wsdl')

    code = ideone_api.create_submission(code, language_id=language_id, std_input="")["link"]
    result = None

    while True:
        time.sleep(1)
        result = ideone_api.submission_details(code)
        if result["status"] in [1,3]:
            continue

        break

    data = { "code" : code }
    if result["stderr"] or result["cmpinfo"]:
        data["output"] = "exception"
        if result["cmpinfo"]:
            data["text"] = result["cmpinfo"]
        elif result["stderr"]:
            data["text"] = result["stderr"]
    else:
        data["output"] = "text"
        data["text"] = result["output"]

    return data


def pageurl(value, language):
    if value.startswith("http"):
        return value
    else:
        return urllib.parse.quote("/%s/%s" % (language, value.replace(' ', '_').replace('.md', '')))


def _wikify_one(language, pat):
    """
    Wikifies one link.
    """
    page_title = pat.group(2)
    if pat.group(1):
        page_name = pat.group(1).rstrip('|')
    else:
        page_name = page_title

    # interwiki
    if ':' in page_name and not page_name.startswith("http"):
        parts = page_name.split(':', 2)
        if page_name == page_title:
            page_title = parts[1]

    link = "<a href='%s'>%s</a>" % (pageurl(page_name, language), page_title)
    return link


def wikify(text, language):
    text, count = WIKI_WORD_PATTERN.subn(functools.partial(_wikify_one, language), text)
    return markdown.markdown(text).strip()


def untab(text):
    lines = text.strip("\n").split("\n")
    if not all([x.startswith("    ") or x == "" for x in lines]):
        return "\n".join(lines)

    return "\n".join([x[4:] if len(x) >= 4 else "" for x in lines])


def init_tutorials():
    contributing_tutorials = wikify(open(os.path.join(os.path.dirname(__file__), "tutorials", "Contributing Tutorials.md")).read(), "en")

    for domain in os.listdir(os.path.join(os.path.dirname(__file__), "tutorials")):
        if domain.endswith(".md"):
            continue

        logging.info("loading data for domain: %s", domain)
        tutorial_data[domain] = {}
        if not os.path.isdir(os.path.join(os.path.dirname(__file__), "tutorials", domain)):
            continue

        if domain not in constants.DOMAIN_DATA:
            logging.warning("skipping domain %s because no domain data exists" % domain)
            continue

        # Ensure English tutorials are preloaded
        if "en" not in tutorial_data[domain]:
            tutorial_data[domain]["en"] = {}

        for language in os.listdir(os.path.join(os.path.dirname(__file__), "tutorials", domain)):
            tutorial_data[domain][language] = {}

            tutorials_path = os.path.join(os.path.dirname(__file__), "tutorials", domain, language)
            if not os.path.isdir(tutorials_path):
                continue

            tutorials = os.listdir(tutorials_path)

            # place the index file first
            tutorials.remove("Welcome.md")
            tutorials = ["Welcome.md"] + tutorials
            for tutorial_file in tutorials:
                if not tutorial_file.endswith(".md"):
                    continue

                tutorial = tutorial_file[:-3]
                logging.debug("loading tutorial %s" % tutorial)

                if not tutorial in tutorial_data[domain][language]:
                    tutorial_data[domain][language][tutorial] = {}

                tutorial_dict = tutorial_data[domain][language][tutorial]

                tutorial_path = os.path.join(os.path.dirname(__file__), "tutorials", domain, language, tutorial_file)

                tutorial_dict["text"] = open(tutorial_path).read().replace("\r\n", "\n")

                if domain == "learnpython.org":
                    # Handle logic specific for `learnpython.org`
                    if "en" not in tutorial_data[domain]:
                        tutorial_data[domain]["en"] = {}

                    # Load translated titles from index.json for learnpython.org
                    index_file_path = os.path.join(tutorials_path, "index.json")
                    try:
                        with open(index_file_path, "r", encoding="utf-8") as f:
                            translated_titles = json.load(f)
                            translated_titles = {key: value for section in translated_titles.values() for key, value in section.items()}
                        logging.info(f"Loaded index.json for language '{language}' from {index_file_path}")
                    except FileNotFoundError:
                        logging.error(f"index.json not found for language '{language}' at {index_file_path}, skipping.")
                        translated_titles = {}

                    # Assign translated or fallback title
                    localized_title = translated_titles.get(tutorial, tutorial)
                    tutorial_dict["page_title"] = localized_title

                    # Extract technical sections (code, output, solution) for learnpython.org
                    sections_match = sections.findall(tutorial_dict["text"])
                    tutorial_dict["text"] = re.sub(
                        r"^Tutorial\s*[\=\-]+\n|Tutorial Code\n[=\-]+\n+(.*)\n*Expected Output\n[=\-]+\n+("
                        r".*)\n*Solution\n[=\-]+\n*(.*)\n*",
                        "", tutorial_dict["text"], flags=re.DOTALL
                    )
                    if sections_match:
                        _, code, output, solution = sections_match[0]
                        tutorial_dict["code"] = untab(code)
                        tutorial_dict["output"] = untab(output)
                        tutorial_dict["solution"] = untab(solution)
                        tutorial_dict["is_tutorial"] = True
                    else:
                        tutorial_dict["code"] = ""
                        tutorial_dict["output"] = ""
                        tutorial_dict["solution"] = ""
                        tutorial_dict["is_tutorial"] = False

                    # Preload English tutorial if needed (for non-English tutorials)
                    if language != "en":
                        english_tutorial_data = tutorial_data[domain]["en"].get(tutorial, {})

                        if not english_tutorial_data:
                            english_tutorial_path = os.path.join(
                                os.path.dirname(__file__), "tutorials", domain, "en", f"{tutorial}.md"
                            )
                            if os.path.isfile(english_tutorial_path):
                                english_text = open(english_tutorial_path, encoding="utf-8").read()
                                sections_match = sections.findall(english_text)
                                if sections_match:
                                    _, code, output, solution = sections_match[0]
                                    english_tutorial_data = {
                                        "code": untab(code),
                                        "output": untab(output),
                                        "solution": untab(solution),
                                        "is_tutorial": True
                                    }
                                    # Store preloaded English content
                                    tutorial_data[domain]["en"][tutorial] = english_tutorial_data

                        # Assign English sections
                        tutorial_dict["code"] = english_tutorial_data.get("code", "")
                        tutorial_dict["output"] = english_tutorial_data.get("output", "")
                        tutorial_dict["solution"] = english_tutorial_data.get("solution", "")
                        tutorial_dict["is_tutorial"] = english_tutorial_data.get("is_tutorial", "")

                    tutorial_dict["text"] = wikify(tutorial_dict["text"], language)

                    # Check if the tutorial has code, output, or solution
                    tutorial_dict["is_tutorial"] = bool(
                        tutorial_dict["code"] or tutorial_dict["output"] or tutorial_dict["solution"]
                    )

                    if tutorial_file == "Welcome.md":
                        tutorial_dict["page_title"] = ''
                    else:
                        if not tutorial_dict["is_tutorial"] and language == "en":
                            logging.warning("File %s/%s/%s is not a tutorial", domain, language, tutorial_file)
                            tutorial_dict["page_title"] = ""
                            tutorial_dict["text"] = wikify(tutorial_dict["text"], language)
                            tutorial_dict["code"] = constants.DOMAIN_DATA[domain]["default_code"]

                    # Update links and navigation for learnpython.org
                    links = [key for key in translated_titles.keys() if key not in CODING_FOR_KIDS_TITLES]
                    tutorial_dict["links"] = [
                        (translated_titles.get(link, link), pageurl(link, language))
                        for link in links
                    ]
                else:
                    # For other domains, standard mechanism for links and navigation
                    tutorial_dict["page_title"] = tutorial

                    # Create links by looking at all lines that are not code lines
                    stripped_text = "\n".join([x for x in tutorial_dict["text"].split("\n") if not x.startswith("    ")])
                    links = [x[0].strip("|") if x[0] else x[1] for x in WIKI_WORD_PATTERN.findall(stripped_text)]
                    tutorial_dict["links"] = [(x, pageurl(x, language)) for x in links]

                    tutorial_sections = sections.findall(tutorial_dict["text"])
                    if tutorial_sections:
                        text, code, output, solution = tutorial_sections[0]
                        tutorial_dict["page_title"] = tutorial
                        tutorial_dict["text"] = wikify(text, language)
                        tutorial_dict["code"] = untab(code)
                        tutorial_dict["output"] = untab(output)
                        tutorial_dict["solution"] = untab(solution)
                        tutorial_dict["is_tutorial"] = True
                    else:
                        if tutorial_file != "Welcome.md":
                            logging.warning("File %s/%s/%s is not a tutorial", domain, language, tutorial_file)
                        tutorial_dict["page_title"] = ""
                        tutorial_dict["text"] = wikify(tutorial_dict["text"], language)
                        tutorial_dict["code"] = constants.DOMAIN_DATA[domain]["default_code"]
                        tutorial_dict["is_tutorial"] = False

                num_links = len(links)
                for link in links:
                    if link not in tutorial_data[domain][language]:
                        tutorial_data[domain][language][link] = {
                            "page_title": link,
                            "text": contributing_tutorials,
                            "code": ""
                        }

                    if not "back_chapter" in tutorial_data[domain][language][link]:
                        tutorial_data[domain][language][link]["back_chapter"] = tutorial.replace(" ", "_")
                    elif not link.startswith("http"):
                        logging.info("Warning! duplicate links to tutorial %s from tutorial %s/%s", link, language, tutorial)

                    page_index = links.index(link)
                    if page_index > 0:
                        if not "previous_chapter" in tutorial_data[domain][language][link]:
                            tutorial_data[domain][language][link]["previous_chapter"] = links[page_index - 1].replace(" ", "_")
                    if page_index < (num_links - 1):
                        if not "next_chapter" in tutorial_data[domain][language][link]:
                            tutorial_data[domain][language][link]["next_chapter"] = links[page_index + 1].replace(" ", "_")
init_tutorials()


def get_languages():
    return sorted(tutorial_data[get_host()].keys())

def get_language_names():
    arr = []
    langs = get_languages()
    for lang in langs:
        arr.append(LANGUAGES.get(lang))
    return arr

def get_host():
    if is_development_mode() or "ondigitalocean.app" in request.host:
        return current_domain

    return request.host[4:] if request.host.startswith("www.") else request.host


def is_development_mode():
    return "localhost" in request.host or "127.0.0.1" in request.host


def get_domain_data():
    data = constants.DOMAIN_DATA[get_host()]
    data["courses"] = courses.get(get_host())
    return data


def get_tutorial_data(tutorial_id, language="en"):
    return tutorial_data[get_host()][language][tutorial_id]


def get_tutorial(tutorial_id, language="en"):
    td = get_tutorial_data(tutorial_id, language)

    if not td:
        return {
            "page_title": html.escape(tutorial_id),
            "text": "Page not found."
        }
    else:
        return td


def error404():
    domain_data = get_domain_data()
    return make_response(render_template(
        "error404.html",
        domain_data=domain_data,
        all_data=constants.DOMAIN_DATA,
        language_code="en",
        languages=get_languages(),
        language_names=get_language_names(),
    ), 404)


# We are checking only the current domain subfolder and adding all the files to files_to_track array
# so that they can be tracked and on change of any of these files, reload the server in real time
def get_filenames_to_watch_and_reload():
    dir_to_look = "tutorials/" + current_domain
    files_to_track = []

    for dirname, dirs, files in os.walk(dir_to_look):
        for filename in files:
            filename = os.path.join(dirname, filename)
            if os.path.isfile(filename):
                files_to_track.append(filename)

    return files_to_track


@app.route("/favicon.ico")
def favicon():
    return open(os.path.join(os.path.dirname(__file__), get_domain_data()["favicon"][1:]), "rb").read()

@app.route("/ads.txt")
def ads():
    return Response(render_template("ads.txt"), mimetype='text/plain')


@app.route("/", methods=["GET", "POST"])
@app.route("/<language>/", methods=["GET", "POST"])
def default_index(language="en"):
    return index("Welcome", language)

@app.route("/about")
@app.route("/privacy")
@app.route("/tos")
def static_file():
    return make_response(render_template(
        request.path.strip("/") + ".html",
        domain_data=get_domain_data(),
        all_data=constants.DOMAIN_DATA,
        domain_data_json=json.dumps(get_domain_data()),
        language_code="en",
    ))

@app.route("/signin")
def signin():
    email = request.args.get("email", None)
    password = request.args.get("password", None)
    user = users.findOne({"email": email})

    if user:
        session["user_id"] = str(user._id)
        return make_response(json.dumps({"status": "error", "error": "no_user"}))

@app.route("/signup")
def signup():
    email = request.args.get("email", None)
    password = request.args.get("password", None)
    confirm = request.args.get("confirm", None)

    if not email or not password or not confirm:
        return make_response(json.dumps({"status": "error", "error": "missing_field"}))

    if not re.findall(r"^[_a-z0-9-]+(\.[_a-z0-9-]+)*@[a-z0-9-]+(\.[a-z0-9-]+)*(\.[a-z]{2,4})$", email):
        return make_response(json.dumps({"status": "error", "error": "invalid_email"}))

    if password != confirm:
        return make_response(json.dumps({"status": "error", "error": "passwords_dont_match"}))

    id = users.insert({
        "email": email,
        "password": password,
    })

    session["user_id"] = str(id)


@app.route("/<language>/progress")
def progress(language):
    return make_response(render_template(
        "progress.html",
        domain_data=get_domain_data(),
        all_data=constants.DOMAIN_DATA,
    ))

@app.route("/<title>", methods=["GET", "POST"])
@app.route("/<language>/<title>", methods=["GET", "POST"])
def index(title, language="en"):
    tutorial = title.replace("_", " ")
    try:
        current_tutorial_data = get_tutorial(tutorial, language)
    except KeyError:
        return error404()
    domain_data = get_domain_data()
    domain_data["language_code"] = language

    if request.method == "GET":
        title_suffix = "Learn %s - Free Interactive %s Tutorial" % (domain_data["language_uppercase"], domain_data["language_uppercase"])
        html_title = "%s - %s" % (title.replace("_", " "), title_suffix) if title != "Welcome" else title_suffix

        if not "uid" in session:
            session["uid"] = binascii.b2a_hex(os.urandom(16))

        uid = session["uid"]

        try:
            site_links = tutorial_data[get_host()][language]["Welcome"]["links"]
        except Exception as e:
            site_links = []
            logging.error("cant get site links for %s %s" % (get_host(), language))

        print(request.host)

        # https://ipinfo.io/1.2.3.4/json - country = DE
        # check if user is German
        # xff = request.headers.get("X-Forwarded-For")
        # if xff and "," in xff:
        #     ip = xff.split(",")[0]
        # else:
        #     ip = request.remote_addr

        # try:
        #     with geoip2.database.Reader('GeoLite2-Country.mmdb') as reader:
        #         response = reader.country(ip)
        # except Exception as e:
        #     logging.exception('error looking up IP %s' % ip)
        #     response = None
        # is_german_user = response and response.country.iso_code == 'DE'

        return make_response(render_template(
            "index-python.html" if (domain_data["language"] == "python") else "index.html",
            tutorial_page=tutorial != "Welcome",
            domain_data=domain_data,
            all_data=constants.DOMAIN_DATA,
            site_tutorial_links=site_links,
            tutorial_data=current_tutorial_data,
            tutorial_data_json=json.dumps(current_tutorial_data),
            domain_data_json=json.dumps(domain_data),
            html_title=html_title,
            language_code=language,
            languages=get_languages(),
            language_names=get_language_names(),
            uid=uid,
            env="dev" if request.host == "localhost:5000" else "prod",
            is_german_user=False,
            **current_tutorial_data
        ))

    # POST method handling
    data = run_code(request.json["code"], domain_data["language_id"])

    if "output" in current_tutorial_data and current_tutorial_data["output"] == data["output"]:
        data["solved"] = True


    else:
        data["solved"] = False

    return make_response(json.dumps(data))


@app.route("/sitemap.xml")
def sitemap_index():
    languages = tutorial_data[get_host()].keys()
    response = make_response(render_template("sitemap_index.xml", languages=languages))
    response.headers['Content-Type'] = 'application/xml'
    return response


@app.route("/sitemap_<language>.xml")
def sitemap(language):
    try:
        titles = tutorial_data[get_host()][language].keys()
    except KeyError:
        return error404()

    # use today as lastmod to make sure the most recent version is always indexed
    lastmod = datetime.datetime.utcnow().date()
    response = make_response(render_template("sitemap.xml", titles=titles, lastmod=lastmod, language=language))
    response.headers['Content-Type'] = 'application/xml'
    return response


@app.route("/robots.txt")
def robots():
    return make_response("User-agent: *\nAllow: /")

if __name__ == "__main__":
    logging.info("listening on port %s", args.port)

    # The extra_files attribute enables us to provide file names which need to be tracked on change, trigger server reload
    app.run(debug=True, port=args.port, host=args.host, extra_files=get_filenames_to_watch_and_reload())
