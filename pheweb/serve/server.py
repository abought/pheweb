
from ..utils import conf, get_phenolist, get_gene_tuples, pad_gene
from ..file_utils import get_generated_path
from .server_utils import get_variant, get_random_page, get_pheno_region
from .autocomplete import Autocompleter
from .auth import GoogleSignIn

from flask import Flask, jsonify, render_template, request, redirect, abort, flash, send_from_directory, send_file, session, url_for
from flask_compress import Compress
from flask_login import LoginManager, UserMixin, login_user, logout_user, current_user

import functools
import re
import traceback
import json


app = Flask(__name__)
Compress(app)
app.config['COMPRESS_LEVEL'] = 2 # Since we don't cache, faster=better
app.config['SECRET_KEY'] = conf.SECRET_KEY if hasattr(conf, 'SECRET_KEY') else 'nonsecret key'
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 9
if 'GOOGLE_ANALYTICS_TRACKING_ID' in conf:
    app.config['GOOGLE_ANALYTICS_TRACKING_ID'] = conf['GOOGLE_ANALYTICS_TRACKING_ID']

if 'custom_templates' in conf:
    app.jinja_loader.searchpath.insert(0, conf.custom_templates)

phenos = {pheno['phenocode']: pheno for pheno in get_phenolist()}


def check_auth(func):
    """
    This decorator for routes checks that the user is authorized (or that no login is required).
    If they haven't, their intended destination is stored and they're sent to get authorized.
    It has to be placed AFTER @app.route() so that it can capture `request.path`.
    """
    if 'login' not in conf:
        return func
    # inspired by <https://flask-login.readthedocs.org/en/latest/_modules/flask_login.html#login_required>
    @functools.wraps(func)
    def decorated_view(*args, **kwargs):
        if current_user.is_anonymous:
            print('unauthorized user {!r} visited the url [{!r}]'.format(current_user, request.path))
            session['original_destination'] = request.path
            return redirect(url_for('get_authorized'))
        assert current_user.email.lower() in conf.login['whitelist'], current_user
        return func(*args, **kwargs)
    return decorated_view


autocompleter = Autocompleter(phenos)
@app.route('/api/autocomplete')
@check_auth
def autocomplete():
    query = request.args.get('query', '')
    suggestions = autocompleter.autocomplete(query)
    if suggestions:
        return jsonify(sorted(suggestions, key=lambda sugg: sugg['display']))
    return jsonify([])

@app.route('/go')
@check_auth
def go():
    query = request.args.get('query', None)
    if query is None:
        die("How did you manage to get a null query?")
    best_suggestion = autocompleter.get_best_completion(query)
    if best_suggestion:
        return redirect(best_suggestion['url'])
    die("Couldn't find page for {!r}".format(query))

@app.route('/api/variant/<query>')
@check_auth
def api_variant(query):
    variant = get_variant(query)
    return jsonify(variant)

@app.route('/variant/<query>')
@check_auth
def variant_page(query):
    try:
        variant = get_variant(query)
        if variant is None:
            die("Sorry, I couldn't find the variant {}".format(query))
        return render_template('variant.html',
                               variant=variant)
    except Exception as exc:
        die('Oh no, something went wrong', exc)

@app.route('/api/manhattan/pheno/<filename>')
@check_auth
def api_pheno(filename):
    return send_from_directory(get_generated_path('manhattan'), filename)

@app.route('/api/top_hits.json')
@check_auth
def api_top_hits():
    return send_file(get_generated_path('top_hits.json'))

@app.route('/api/qq/pheno/<filename>')
@check_auth
def api_pheno_qq(filename):
    return send_from_directory(get_generated_path('qq'), filename)

@app.route('/top_hits')
@check_auth
def top_hits_page():
    return render_template('top_hits.html')

@app.route('/random')
@check_auth
def random_page():
    url = get_random_page()
    if url is None:
        die("Sorry, it looks like no hits in this pheweb reached the significance threshold.")
    return redirect(url)

@app.route('/pheno/<phenocode>')
@check_auth
def pheno_page(phenocode):
    try:
        pheno = phenos[phenocode]
    except:
        die("Sorry, I couldn't find the pheno code {!r}".format(phenocode))
    return render_template('pheno.html',
                           phenocode=phenocode,
                           pheno=pheno,
    )



@app.route('/region/<phenocode>/<region>')
@check_auth
def region_page(phenocode, region):
    try:
        pheno = phenos[phenocode]
    except:
        die("Sorry, I couldn't find the phewas code {!r}".format(phenocode))
    pheno['phenocode'] = phenocode
    return render_template('region.html',
                           pheno=pheno,
                           region=region,
    )

@app.route('/api/region/<phenocode>/lz-results/') # This API is easier on the LZ side.
@check_auth
def api_region(phenocode):
    filter_param = request.args.get('filter')
    groups = re.match(r"analysis in 3 and chromosome in +'(.+?)' and position ge ([0-9]+) and position le ([0-9]+)", filter_param).groups()
    chrom, pos_start, pos_end = groups[0], int(groups[1]), int(groups[2])
    rv = get_pheno_region(phenocode, chrom, pos_start, pos_end)
    return jsonify(rv)


@functools.lru_cache(None)
def get_gene_region_mapping():
    return {genename: (chrom, pos1, pos2) for chrom, pos1, pos2, genename in get_gene_tuples()}

@functools.lru_cache(None)
def get_best_phenos_by_gene():
    with open(get_generated_path('best-phenos-by-gene.json')) as f:
        return json.load(f)

@app.route('/region/<phenocode>/gene/<genename>')
@check_auth
def gene_phenocode_page(phenocode, genename):
    try:
        gene_region_mapping = get_gene_region_mapping()
        chrom, start, end = gene_region_mapping[genename]

        include_string = request.args.get('include', '')
        if include_string:
            include_chrom, include_pos = include_string.split('-')
            include_pos = int(include_pos)
            assert include_chrom == chrom
            if include_pos < start:
                start = include_pos - (end - start) * 0.01
            elif include_pos > end:
                end = include_pos + (end - start) * 0.01
        start, end = pad_gene(start, end)

        pheno = phenos[phenocode]

        phenos_in_gene = []
        for pheno_in_gene in get_best_phenos_by_gene().get(genename, []):
            phenos_in_gene.append({
                'pheno': {k:v for k,v in phenos[pheno_in_gene['phenocode']].items() if k not in ['assoc_files', 'colnum']},
                'assoc': {k:v for k,v in pheno_in_gene.items() if k != 'phenocode'},
            })

        return render_template('gene.html',
                               pheno=pheno,
                               significant_phenos=phenos_in_gene,
                               gene_symbol=genename,
                               region='{}:{}-{}'.format(chrom, start, end))
    except Exception as exc:
        die("Sorry, your region request for phenocode {!r} and gene {!r} didn't work".format(phenocode, genename), exception=exc)


@app.route('/gene/<genename>')
@check_auth
def gene_page(genename):
    phenos_in_gene = get_best_phenos_by_gene().get(genename, [])
    if not phenos_in_gene:
        die("Sorry, that gene doesn't appear to have any associations in any phenotype")
    return gene_phenocode_page(phenos_in_gene[0]['phenocode'], genename)


@app.route('/')
def homepage():
    return render_template('index.html')

@app.route('/about')
def about_page():
    return render_template('about.html')

def die(message='no message', exception=None):
    if exception is not None:
        print(exception)
        traceback.print_exc()
    print(message)
    flash(message)
    abort(404)

@app.errorhandler(404)
def error_page(message):
    return render_template(
        'error.html',
        message=message
    ), 404

# Resist some CSRF attacks
@app.after_request
def apply_caching(response):
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


### OAUTH2
if 'login' in conf:
    google_sign_in = GoogleSignIn(app)


    lm = LoginManager(app)
    lm.login_view = 'homepage'

    class User(UserMixin):
        "A user's id is their email address."
        def __init__(self, username=None, email=None):
            self.username = username
            self.email = email
        def get_id(self):
            return self.email
        def __repr__(self):
            return "<User email={!r}>".format(self.email)

    @lm.user_loader
    def load_user(id):
        print('id', id)
        if id in conf.login['whitelist']:
            return User(email=id)
        return None


    @app.route('/logout')
    def logout():
        print('logging out user {!r}'.format(current_user))
        logout_user()
        return redirect(url_for('homepage'))

    @app.route('/login_with_google')
    def login_with_google():
        "this route is for the login button"
        session['original_destination'] = url_for('homepage')
        return redirect(url_for('get_authorized'))

    @app.route('/get_authorized')
    def get_authorized():
        "This route tries to be clever and handle lots of situations."
        if current_user.is_anonymous:
            return google_sign_in.authorize()
        else:
            if 'original_destination' in session:
                orig_dest = session['original_destination']
                del session['original_destination'] # We don't want old destinations hanging around.  If this leads to problems with re-opening windows, disable this line.
            else:
                orig_dest = url_for('homepage')
            return redirect(orig_dest)

    @app.route('/callback/google')
    def oauth_callback_google():
        if not current_user.is_anonymous:
            return redirect(url_for('homepage'))
        try:
            username, email = google_sign_in.callback() # oauth.callback reads request.args.
        except Exception as exc:
            print('Error in google_sign_in.callback():')
            print(exc)
            print(traceback.format_exc())
            flash('Something is wrong with authentication.  Please email pjvh@umich.edu')
            return redirect(url_for('homepage'))
        if email is None:
            # I need a valid email address for my user identification
            flash('Authentication failed by failing to get an email address.  Please email pjvh@umich.edu')
            return redirect(url_for('homepage'))

        if email.lower() not in conf.login['whitelist']:
            flash('Your email, {!r}, is not in the list of allowed emails.'.format(email))
            redirect(url_for('homepage'))

        # Log in the user, by default remembering them for their next visit.
        user = User(username, email)
        login_user(user, remember=True)

        return redirect(url_for('get_authorized'))
