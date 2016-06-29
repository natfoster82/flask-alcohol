"""
    Flask-Alcohol
    --------------
    Automatically generate API routes from Flask-SQLAlchemy models.
    :copyright: (c) 2016 by Nat Foster.
    :copyright: (c) 2013 by Freedom Dumlao.
    :license: BSD, see LICENSE for more details.
"""

__version__ = '0.1'


from flask import request, jsonify, make_response, current_app, Response, get_flashed_messages, g, flash
from sqlalchemy.orm import class_mapper, joinedload
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.orm.properties import ColumnProperty
from sqlalchemy.orm.collections import InstrumentedList
from werkzeug.routing import parse_rule
import inspect
import functools
import re


def route(rule, **options):
    """
    A decorator that is used to define custom routes for methods in
    Router subclasses. The format is exactly the same as Flask's
    `@app.route` decorator except that it supports an is_auto kwarg that
    APIMixin uses for autoroutes.
    """
    try:
        is_auto = bool(options.pop('is_auto'))
    except KeyError:
        is_auto = False

    def decorator(f):
        # Put the rule cache on the method itself instead of globally
        if not hasattr(f, '_rule_cache') or f._rule_cache is None:
            f._rule_cache = {f.__name__: [(rule, options)]}
        elif not f.__name__ in f._rule_cache:
            f._rule_cache[f.__name__] = [(rule, options)]
        else:
            f._rule_cache[f.__name__].append((rule, options))

        f._rule_cache['is_auto'] = is_auto

        return f

    return decorator


def authorizes(*route_names):
    """
    Decorates a static method that takes the resource as performs security checks on any number of routes
    """

    def decorator(f):
        # Put the check cache on the method itself instead of globally
        f._check_cache = route_names
        return f

    return decorator


def before_return(*route_names):
    """
    Decorates a static method that takes the resource and executes custom code before committing changes
    and returning the json. Can be applied to any number of auto routes.
    """

    def decorator(f):
        # Put the check cache on the method itself instead of globally
        f._before_cache = route_names
        return f

    return decorator


def adjusts_query(*route_names):
    """
    Decorates a static method that takes the query and adds any necessary statements before fetching the result(s)
    """

    def decorator(f):
        # Put the check cache on the method itself instead of globally
        f._adjuster_cache = route_names
        return f

    return decorator


def setter(*field_names):
    """
    Decorates a method that validates and sets any number of fields in the object
    """

    def decorator(f):
        # Put the check cache on the method itself instead of globally
        f._setter_cache = field_names
        return f

    return decorator


def getter(*field_names):
    """
    Decorates a method that returns a transformed value for any number of fields in the object
    """

    def decorator(f):
        # Put the check cache on the method itself instead of globally
        f._getter_cache = field_names
        return f

    return decorator


def extra_field(info=None):
    """
    Decorates a method that represents an extra field in the json
    """

    def decorator(f):
        # Put the check cache on the method itself instead of globally
        f._extra_cache = info or {}
        return f

    return decorator


def api_messages():
    messages = get_flashed_messages()
    default_message = current_app.config.get('API_ERROR_MESSAGE')
    if default_message:
        messages.append(default_message)
    return messages


class Router(object):
    """
    Base object that provides the registration methods for the APIMixin and APIMeta classes.
    Heavily lifted from Flask-Classy.
    """

    __decorators__ = []
    __routebase__ = None
    __routeprefix__ = None

    # methods modified from Flask-Classy
    @classmethod
    def register(cls, app, subdomain=None):
        """
        Code copied and simplified from the excellent Flask-Classy.
        Registers an APIMixin class for use with a specific instance of a
        Flask app. Only methods with the @route decorator are candidates
        to be routed and will have routes registered when this method is
        called.

        :param app: an instance of a Flask application

        :param subdomain:  A subdomain that this registration should use when
                           configuring routes.
        """

        if cls in (Router, APIMixin):
            raise TypeError("cls must be a subclass of Router or APIMixin, not one of the base classes themselves")

        app.config.setdefault('ROUTE_PREFIX', None)

        if not subdomain:
            if hasattr(app, "subdomain") and app.subdomain is not None:
                subdomain = app.subdomain

        # go through all the members of the class and add rules for those with a @route decorator
        with app.app_context():
            for name, value in inspect.getmembers(cls):
                if hasattr(value, '_rule_cache') and name in value._rule_cache \
                        and (not value._rule_cache['is_auto'] or name in cls.__autoroutes__):

                    proxy = cls.make_proxy_method(name)
                    route_name = cls.build_route_name(name)
                    for idx, cached_rule in enumerate(value._rule_cache[name]):
                        rule, options = cached_rule
                        rule = cls.build_rule(rule, app.config['ROUTE_PREFIX'])
                        sub, ep, options = cls.parse_options(options)

                        if not subdomain and sub:
                            subdomain = sub

                        if ep:
                            endpoint = ep
                        elif len(value._rule_cache[name]) == 1:
                            endpoint = route_name
                        else:
                            endpoint = "%s_%d" % (route_name, idx,)

                        app.add_url_rule(rule, endpoint, proxy, subdomain=subdomain, **options)

    @classmethod
    def parse_options(cls, options):
        """
        Extracts subdomain and endpoint values from the options dict and returns
        them along with a new dict without those values.
        """
        options = options.copy()
        subdomain = options.pop('subdomain', None)
        endpoint = options.pop('endpoint', None)
        return subdomain, endpoint, options,

    @classmethod
    def make_proxy_method(cls, name):
        """
        Creates a proxy function that can be used by Flasks routing. The
        proxy instantiates the FlaskView subclass and calls the appropriate
        method.

        :param name: the name of the method to create a proxy for
        """

        i = cls()
        view = getattr(i, name)

        if cls.__decorators__:
            for decorator in cls.__decorators__:
                view = decorator(view)

        @functools.wraps(view)
        def proxy(**forgettable_view_args):
            # Always use the global request object's view_args, because they
            # can be modified by intervening function before an endpoint or
            # wrapper gets called. This matches Flask's behavior.
            del forgettable_view_args

            response = view(**request.view_args)
            if not isinstance(response, Response):
                response = make_response(response)

            return response

        return proxy

    @classmethod
    def build_rule(cls, rule, prefix):
        """
        Creates a routing rule based on either the class name (minus the
        'View' suffix) or the defined `route_base` attribute of the class

        :param rule: the path portion that should be appended to the
                     route base

        """

        rule_parts = []
        route_prefix = cls.__routeprefix__ or prefix
        if route_prefix:
            rule_parts.append(route_prefix)

        route_base = cls.get_route_base()
        if route_base:
            rule_parts.append(route_base)

        rule_parts.append(rule)

        result = "/%s" % "/".join(rule_parts)
        rule = re.sub(r'(/)\1+', r'\1', result)
        return rule.rstrip('/')

    @classmethod
    def get_route_base(cls):
        """Returns the route base to use for the current class."""

        if cls.__routebase__ is not None:
            route_base = cls.__routebase__
            base_rule = parse_rule(route_base)
            cls.base_args = [r[2] for r in base_rule]
        elif hasattr(cls, '__tablename__') and cls.__tablename__ is not None:
            route_base = cls.__tablename__
        else:
            route_base = cls.__name__.lower()

        return route_base.strip('/')

    @classmethod
    def build_route_name(cls, method_name):
        """
        Creates a unique route name based on the combination of the class
        name with the method name.

        :param method_name: the method name to use when building a route name
        """
        return cls.__name__ + ":%s" % method_name


class APIMixin(Router):
    """
    Mixin to use with Flask-SQLAlchemy's db.Model to auto generate a simple API and to provide Flask route functions
    """

    # would it be more acceptable to split this into two mixins, one for the model and one for the routes?

    __autoroutes__ = []
    __columndefaults__ = {
        'public': True, # set to false if you want it to be totally unavailable to the user
        'defer': False, # if True, can still be accessed with the include request argument
        'set_by': None, # can be json, url, or server
        'example': None,
        'input_type': None,
        'label': None
    }
    __relationshipdefaults__ = {
        'public': False, # unlike columns, relationships are private by default
        'defer': True, # strongly suggest you keep this as true for relationships to avoid huge chains
        'set_by': None, # relationships don't yet support setting from another table's api
        'example': None,
        'input_type': None,
        'label': None
    }
    __idattr__ = 'id'
    __maxresults__ = None
    __sort__ = None

    @classmethod
    def register(cls, app, subdomain=None):
        """
        Calls the Router class's register method on the app. This adds more stuff for
        generating the API.

        :param app: an instance of a Flask application

        :param subdomain:  A subdomain that this registration should use when
                           configuring routes.
        """

        if cls in (Router, APIMixin):
            raise TypeError("cls must be a subclass of RoutingBase or APIMixin, not one of the base classes themselves")

        if not subdomain:
            if hasattr(app, "subdomain") and app.subdomain is not None:
                subdomain = app.subdomain

        super(APIMixin, cls).register(app, subdomain)

        app.config.setdefault('API_ERROR_MESSAGE', None)

        cls.__security__ = {}
        cls.__beforereturns__ = {}
        cls.__adjusters__ = {}
        cls.__setters__ = {}
        cls.__getters__ = {}
        cls.__infos__ = {}
        cls.__metas__ = {}
        cls.__defaultfields__ = set([])
        cls.__indexedfields__ = set([])
        cls.__lazyrelationships__ = set([])

        # go through all the members of the class and add filters for columns with default filters,
        # setters for all those with a @setter decorator,
        # and getters for all those with a @getter decorator

        with app.app_context():
            mapper = class_mapper(cls)
            if mapper.polymorphic_map:
                # do something that works here
                pass
            for name, value in inspect.getmembers(cls):
                if type(value) == InstrumentedAttribute and not name.startswith('_'):
                    # the _ is to avoid doubling fields
                    # find out whether it is a relationship or column
                    if type(value.comparator) == ColumnProperty.Comparator:
                        api_info = cls.__columndefaults__.copy()
                        api_info.update(value.comparator.info)
                        indexed = bool(value.comparator.primary_key or value.comparator.index)
                        if indexed:
                            cls.__indexedfields__.add(name)
                        editable = api_info['set_by'] == 'json'
                    else:
                        # it is a relationship
                        api_info = cls.__relationshipdefaults__.copy()
                        api_info.update(value.comparator.info)
                        if value.comparator.property.lazy == True:
                            cls.__lazyrelationships__.add(name)
                        indexed = False
                        editable = False

                    cls.__infos__[name] = api_info
                    if api_info['public'] and not api_info['defer']:
                        cls.__defaultfields__.add(name)
                    # make the meta
                    meta_dict = {
                        'indexed': indexed,
                        'editable': editable,
                    }
                    if editable:
                        meta_dict['input_type'] = cls._predict_input_type(api_info, value.comparator)
                        meta_dict['required'] = not value.comparator.nullable
                    cls.__metas__[name] = meta_dict

                elif hasattr(value, '_extra_cache'):
                    info = value.__dict__['_extra_cache']
                    api_info = cls.__columndefaults__.copy()
                    api_info['indexed'] = False
                    api_info.update(info)
                    if api_info['public']:
                        cls.__getters__[name] = name
                        cls.__infos__[name] = api_info
                        if not api_info['defer']:
                            cls.__defaultfields__.add(name)
                        cls.__metas__[name] = {
                            'indexed': False,
                            'editable': False
                        }

                elif hasattr(value, '_check_cache'):
                    route_names = value.__dict__['_check_cache']
                    for route_name in route_names:
                        try:
                            cls.__security__[route_name].append(name)
                        except KeyError:
                            cls.__security__[route_name] = [name]

                elif hasattr(value, '_before_cache'):
                    route_names = value.__dict__['_before_cache']
                    for route_name in route_names:
                        try:
                            cls.__beforereturns__[route_name].append(name)
                        except KeyError:
                            cls.__beforereturns__[route_name] = [name]

                elif hasattr(value, '_adjuster_cache'):
                    route_names = value.__dict__['_adjuster_cache']
                    for route_name in route_names:
                        try:
                            cls.__adjusters__[route_name].append(name)
                        except KeyError:
                            cls.__adjusters__[route_name] = [name]

                elif hasattr(value, '_setter_cache'):
                    field_names = value.__dict__['_setter_cache']
                    for field_name in field_names:
                        cls.__setters__[field_name] = name

                elif hasattr(value, '_getter_cache'):
                    field_names = value.__dict__['_getter_cache']
                    for field_name in field_names:
                        cls.__getters__[field_name] = name

    @classmethod
    def _predict_input_type(cls, api_info, comparator):
        if api_info['input_type']:
            return api_info['input_type']
        try:
            column_str = str(comparator.property.columns[0].type)
        except AttributeError:
            # it is a relationship
            return None
        if column_str.startswith('VARCHAR'):
            return 'text'
        if column_str == 'TEXT':
            return 'textarea'
        if column_str.startswith('INTEGER') or column_str.startswith('FLOAT'):
            return 'number'
        if column_str == 'DATETIME':
            return 'datetime'
        if column_str == 'BOOLEAN':
            return 'checkbox'

    @classmethod
    def _get_included_fields(cls):
        try:
            fields = g.cached_included_fields[cls.__name__]
        except KeyError:
            only_fields = request.args.get('only')
            if only_fields:
                fields = set([x for x in only_fields.split(',') if x in cls.__infos__])
            else:
                fields = cls.__defaultfields__
                include_fields = request.args.get('include')
                if include_fields:
                    include_fields = set([x for x in include_fields.split(',') if x in cls.__infos__])
                    fields = fields | include_fields

                defer_fields = request.args.get('defer')
                if defer_fields:
                    defer_fields = set(defer_fields.split(','))
                    fields = fields - defer_fields
            g.cached_included_fields[cls.__name__] = fields
        return fields

    @classmethod
    def _get_included_relationships(cls):
        included_fields = cls._get_included_fields()
        included_relationships = []
        for rel in cls.__lazyrelationships__:
            if rel in included_fields:
                included_relationships.append(rel)
        return included_relationships

    @classmethod
    def _joinedload_query(cls, query):
        jloads = []
        for rel in cls._get_included_relationships():
            jload = joinedload(rel)
            jloads.append(jload)
        if jloads:
            return query.options(*jloads)
        return query

    @classmethod
    def _get_api_info(cls, name):
        return cls.__infos__[name]

    @classmethod
    def _get_obj_by_id(cls, identifier, route):
        id_col = getattr(cls, cls.__idattr__)
        query = cls.query.filter(id_col == identifier)
        query = cls._joinedload_query(query)
        query = cls._adjust_query(query, route)
        return query.first()

    @classmethod
    def _get_results(cls, route='index'):
        query = cls.query
        for field in cls.__indexedfields__:
            filter_string = request.args.get(field)
            if filter_string:
                column = getattr(cls, field)
                filter_list = filter_string.split(',')
                filter_list = [x if x != 'null' else None for x in filter_list]
                if len(filter_list) > 1:
                    query = query.filter(column.in_(filter_list))
                else:
                    query = query.filter(column == filter_list[0])

        sort_rules = request.args.get('sort') or cls.__sort__
        if sort_rules:
            rules = sort_rules.split(',')
            for rule in rules:
                if rule[0] == '-':
                    col_name = rule[1:]
                    desc = True
                else:
                    col_name = rule
                    desc = False
                col = getattr(cls, col_name, None)
                if col and (col.comparator.primary_key or col.comparator.index):
                    order_by = col
                    if desc:
                        order_by = col.desc()
                else:
                    return jsonify(messages=api_messages()), 400

                query = query.order_by(order_by)

        per_page = request.args.get('per_page')
        if per_page is None:
            per_page = cls.__maxresults__
        elif cls.__maxresults__ and int(per_page) > cls.__maxresults__:
            return jsonify(messages=api_messages()), 400

        # adjust the query further before pagination
        query = cls._joinedload_query(query)
        query = cls._adjust_query(query, route)

        if per_page is None:
            objects = query.all()
            total = len(objects)
            has_next = False
        else:
            page = request.args.get('page') or 1
            page_results = query.paginate(int(page), int(per_page))
            objects = page_results.items
            total = page_results.total
            has_next = page_results.has_next
        return objects, total, has_next

    # a little confusion here on what to use, class, static, or normal methods
    # same goes for routes, by that thinking
    # these definitely should be classmethods, i think, but the decorated authorisers and adjusters should be static
    @classmethod
    def _authorize(cls, route_name, resource=None):
        func_names = cls.__security__.get(route_name) or []
        for func_name in func_names:
            check = getattr(cls, func_name)
            if not check(resource):
                return False
        return True

    @classmethod
    def _before_return(cls, route_name, resource=None):
        befores = cls.__beforereturns__.get(route_name) or []
        for before in befores:
            before_func = getattr(cls, before)
            before_func(resource)

    @classmethod
    def _adjust_query(cls, query, route):
        query_adjusters = cls.__adjusters__.get(route) or []
        for adjuster in query_adjusters:
            adjuster_func = getattr(cls, adjuster)
            query = adjuster_func(query)
        return query

    def _auto_get(self, name):
        value = getattr(self, name)
        if type(value) == InstrumentedList:
            value = [x.as_dict(use_defaults=True) for x in value]
        else:
            try:
                value = value.as_dict(use_defaults=True)
            except AttributeError:
                pass
        return value

    def _auto_set(self, name, value):
        # assumes it has passed validation or is set by server
        # handle dates/times/datetimes
        # handle mutable postgres types like JSON, JSONB, and ARRAY
        setattr(self, name, value)

    def _set_field_value(self, name, value=None, try_auto=True):
        try:
            func_name = self.__setters__[name]
            func = getattr(self, func_name)
        except (KeyError, AttributeError):
            if not try_auto:
                return None
            func = self._auto_set
        try:
            func(name, value)
        except (ValueError, AssertionError):
            g.failed_validation = True

    def _get_field_value(self, name):
        if name in self.__getters__:
            func_name = self.__getters__[name]
            func = getattr(self, func_name)
            try:
                field_value = func(name)
            except TypeError:
                field_value = func()
            return field_value
        return self._auto_get(name)

    def _auto_update(self, mapper=None):
        cls = self.__class__
        if mapper is None:
            mapper = class_mapper(cls)
        for col in mapper.columns:
            # this order is the listed order except that hybrid properties go first
            api_info = cls._get_api_info(col.name)
            set_by = api_info['set_by']
            if set_by == 'json':
                if col.name in g.fields:
                    val = g.fields.get(col.name)
                    self._set_field_value(col.name, val)
            elif set_by == 'url':
                if col.name in request.view_args:
                    val = request.view_args.get(col.name)
                    self._set_field_value(col.name, val)
            elif set_by == 'server':
                # important not to let it try to set it from json
                # requires a @setter decorated function
                self._set_field_value(name=col.name, try_auto=False)

    @staticmethod
    def _get_sql_session():
        return current_app.extensions['sqlalchemy'].db.session

    @classmethod
    def set_g(cls):
        g.fields = request.json or request.form
        g.failed_validation = False
        g.cached_included_fields = {}

    @classmethod
    @route('', is_auto=True)
    def index(cls, **kwargs):
        cls.set_g()
        # this kwargs stuff is there for when there are arguments in the prefix or base.
        # how did flask-classy solve this?
        if not cls._authorize('index', resource=None):
            return jsonify(messages=api_messages()), 403
        objects, total, has_next = cls._get_results()
        cls._before_return('index', objects)
        if g.failed_validation:
            return jsonify(messages=api_messages()), 400
        return jsonify(results=[x.as_dict(use_defaults=False) for x in objects],
                       total=total,
                       has_next=has_next)

    @classmethod
    @route('/<identifier>', is_auto=True)
    def get(cls, **kwargs):
        cls.set_g()
        obj = cls._get_obj_by_id(kwargs['identifier'], 'get')
        if obj is None:
            return jsonify(messages=api_messages()), 404
        if not cls._authorize('get', resource=obj):
            return jsonify(messages=api_messages()), 403
        cls._before_return('get', obj)
        if g.failed_validation:
            return jsonify(messages=api_messages()), 400
        return jsonify(obj.as_dict(use_defaults=False))

    @classmethod
    @route('', methods=['POST'], is_auto=True)
    def post(cls, **kwargs):
        cls.set_g()
        if not cls._authorize('post', resource=None):
            return jsonify(messages=api_messages()), 403
        obj = cls()
        obj._auto_update()
        cls._before_return('post', obj)
        if g.failed_validation:
            return jsonify(messages=api_messages()), 400
        session = cls._get_sql_session()
        session.add(obj)
        session.commit()
        response = jsonify(obj.as_dict(use_defaults=False))
        response.status_code = 201
        response.headers['Location'] = obj.get_location()
        return response

    @classmethod
    @route('/<identifier>', methods=['PUT'], is_auto=True)
    def put(cls, **kwargs):
        cls.set_g()
        # pragmatic put method that does not require the whole object to be sent back
        obj = cls._get_obj_by_id(kwargs['identifier'], 'put')
        if obj is None:
            return jsonify(messages=api_messages()), 404
        if not cls._authorize('put', resource=obj):
            return jsonify(messages=api_messages()), 403
        obj._auto_update()
        cls._before_return('put', obj)
        if g.failed_validation:
            return jsonify(messages=api_messages()), 400
        session = cls._get_sql_session()
        session.commit()
        return jsonify(obj.as_dict(use_defaults=False))

    @classmethod
    @route('/<identifier>', methods=['DELETE'], is_auto=True)
    def delete(cls, **kwargs):
        cls.set_g()
        obj = cls._get_obj_by_id(kwargs['identifier'], 'delete')
        if obj is None:
            return jsonify(messages=api_messages()), 404
        if not cls._authorize('delete', resource=obj):
            return jsonify(messages=api_messages()), 403
        cls._before_return('delete', obj)
        if g.failed_validation:
            return jsonify(messages=api_messages()), 400
        session = cls._get_sql_session()
        session.delete(obj)
        session.commit()
        return jsonify(), 204

    @classmethod
    @route('/meta', is_auto=True)
    def meta(cls, **kwargs):
        if not cls._authorize('meta'):
            return jsonify(messages=api_messages()), 403
        cls._before_return('meta')
        return jsonify(cls.__metas__)

    def as_dict(self, use_defaults=True):
        if use_defaults:
            # don't listen to the request and only return the default fields
            fields = self.__defaultfields__
        else:
            fields = self.__class__._get_included_fields()

        result_dict = {}
        for field in fields:
            result_dict[field] = self._get_field_value(field)

        add_dict = self.more_json()
        for key in add_dict:
            result_dict[key] = add_dict[key]
        return result_dict

    def more_json(self):
        return {}

    def get_location(self):
        return '/api/url/goes/here'


class UserRouteMixin(Router):
    # could be cool
    pass


class APIMeta(Router):
    __routebase__ = 'meta'
    __routeprefix__ = 'api'

    @classmethod
    @route('')
    def get(cls):
        rules = []
        for rule in current_app.url_map.iter_rules():
            rule_str = str(rule).strip('/')
            prefix_str = cls.__routeprefix__ or current_app.config.get('ROUTE_PREFIX')
            prefix_str = prefix_str.strip('/')
            if not cls.__routeprefix__ or rule_str.startswith(prefix_str):
                rules.append([str(rule), list(rule.methods)])
        return jsonify(rules=rules)

