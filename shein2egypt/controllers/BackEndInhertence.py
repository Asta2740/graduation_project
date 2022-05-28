import base64
import urllib

import requests

from odoo import api
from odoo import http
from odoo.addons.http_routing.models.ir_http import slug
from odoo.addons.website.models.ir_http import sitemap_qs2dom
from odoo.http import request
from odoo.addons.website_sale.controllers.main import WebsiteSale, TableCompute
import undetected_chromedriver as uc
from selenium.webdriver.chrome.options import Options
from dataclasses import dataclass
from odoo.addons.website_sale.controllers.main import WebsiteSale
# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
import json
import logging
from werkzeug.exceptions import Forbidden, NotFound
from werkzeug.urls import url_decode, url_encode, url_parse

from odoo import fields, http, SUPERUSER_ID, tools, _
from odoo.fields import Command
from odoo.http import request
from odoo.addons.base.models.ir_qweb_fields import nl2br
from odoo.addons.http_routing.models.ir_http import slug
from odoo.addons.payment.controllers import portal as payment_portal
from odoo.addons.payment.controllers.post_processing import PaymentPostProcessing
from odoo.addons.website.controllers.main import QueryURL
from odoo.addons.website.models.ir_http import sitemap_qs2dom
from odoo.exceptions import AccessError, MissingError, ValidationError
from odoo.addons.portal.controllers.portal import _build_url_w_params
from odoo.addons.website.controllers import main
from odoo.addons.website.controllers.form import WebsiteForm
from odoo.osv import expression
from odoo.tools.json import scriptsafe as json_scriptsafe
from dataclasses import dataclass

_logger = logging.getLogger(__name__)


@dataclass
class Product:
    price: str = None
    size1: str = None
    size2: str = None
    size3: str = None
    size4: str = None
    size5: str = None
    size6: str = None
    counterT: str = None


def __init__(self):
    self.table = {}


def _check_place(self, posx, posy, sizex, sizey, ppr):
    res = True
    for y in range(sizey):
        for x in range(sizex):
            if posx + x >= ppr:
                res = False
                break
            row = self.table.setdefault(posy + y, {})
            if row.setdefault(posx + x) is not None:
                res = False
                break
        for x in range(ppr):
            self.table[posy + y].setdefault(x, None)
    return res


def process(self, products, ppg=20, ppr=4):
    # Compute products positions on the grid
    minpos = 0
    index = 0
    maxy = 0
    x = 0
    for p in products:
        x = min(max(p.website_size_x, 1), ppr)
        y = min(max(p.website_size_y, 1), ppr)
        if index >= ppg:
            x = y = 1

        pos = minpos
        while not self._check_place(pos % ppr, pos // ppr, x, y, ppr):
            pos += 1
        # if 21st products (index 20) and the last line is full (ppr products in it), break
        # (pos + 1.0) / ppr is the line where the product would be inserted
        # maxy is the number of existing lines
        # + 1.0 is because pos begins at 0, thus pos 20 is actually the 21st block
        # and to force python to not round the division operation
        if index >= ppg and ((pos + 1.0) // ppr) > maxy:
            break

        if x == 1 and y == 1:  # simple heuristic for CPU optimization
            minpos = pos // ppr

        for y2 in range(y):
            for x2 in range(x):
                self.table[(pos // ppr) + y2][(pos % ppr) + x2] = False
        self.table[pos // ppr][pos % ppr] = {
            'product': p, 'x': x, 'y': y,
            'ribbon': p._get_website_ribbon(),
        }
        if index <= ppg:
            maxy = max(maxy, y + (pos // ppr))
        index += 1

    # Format table according to HTML needs
    rows = sorted(self.table.items())
    rows = [r[1] for r in rows]
    for col in range(len(rows)):
        cols = sorted(rows[col].items())
        x += len(cols)
        rows[col] = [r[1] for r in cols if r[1]]

    return rows


class WebsiteSale_inhernet(WebsiteSale):

    def _get_pricelist_context(self):
        pricelist_context = dict(request.env.context)
        pricelist = False
        if not pricelist_context.get('pricelist'):
            pricelist = request.website.get_current_pricelist()
            pricelist_context['pricelist'] = pricelist.id
        else:
            pricelist = request.env['product.pricelist'].browse(pricelist_context['pricelist'])

        return pricelist_context, pricelist

    def _get_search_order(self, post):
        # OrderBy will be parsed in orm and so no direct sql injection
        # id is added to be sure that order is a unique sort key
        order = post.get('order') or 'website_sequence ASC'
        return 'is_published desc, %s, id desc' % order

    def _get_search_domain(self, search, category, attrib_values, search_in_description=True):
        domains = [request.website.salex_domain()]
        if search:
            for srch in search.split(" "):
                subdomains = [
                    [('name', 'ilike', srch)],
                    [('product_variant_ids.default_code', 'ilike', srch)]
                ]
                if search_in_description:
                    subdomains.append([('description', 'ilike', srch)])
                    subdomains.append([('description_sale', 'ilike', srch)])
                domains.append(expression.OR(subdomains))

        if category:
            domains.append([('public_categ_ids', 'child_of', int(category))])

        if attrib_values:
            attrib = None
            ids = []
            for value in attrib_values:
                if not attrib:
                    attrib = value[0]
                    ids.append(value[1])
                elif value[0] == attrib:
                    ids.append(value[1])
                else:
                    domains.append([('attribute_line_ids.value_ids', 'in', ids)])
                    attrib = value[0]
                    ids = [value[1]]
            if attrib:
                domains.append([('attribute_line_ids.value_ids', 'in', ids)])

        return expression.AND(domains)

    def sitemap_shop(env, rule, qs):
        if not qs or qs.lower() in '/shop':
            yield {'loc': '/shop'}

        Category = env['product.public.category']
        dom = sitemap_qs2dom(qs, '/shop/category', Category._rec_name)
        dom += env['website'].get_current_website().website_domain()
        for cat in Category.search(dom):
            loc = '/shop/category/%s' % slug(cat)
            if not qs or qs.lower() in loc:
                yield {'loc': loc}

    def updatexs(self, url):
        counter = 0

        options = Options()
        options.add_argument('--headless')
        options.add_argument('--disable-gpu')
        driver = uc.Chrome(options=options)
        driver.get(url)
        try:
            price = driver.find_element_by_xpath(
                '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[1]/div[2]/div/div/span').text
        except:
            price = driver.find_element_by_xpath(
                '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[1]/div[3]/div/div/span').text
        try:
            counter = counter + 1

            check_if_sold_out = driver.find_element_by_xpath(
                '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[1]/span/div').get_attribute(
                "class")
            if 'radio_soldout' in check_if_sold_out:
                size1 = 'Nothing'

            else:

                size1 = driver.find_element_by_xpath(
                    '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[1]/span/div/div').text

                if 'XS - L' in size1:
                    check_if_sold_out = driver.find_element_by_xpath(
                        '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[2]/span/div').get_attribute(
                        "class")
                    if 'radio_soldout' in check_if_sold_out:
                        size1 = 'Nothing'
                    else:
                        size1 = driver.find_element_by_xpath(
                            '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[2]/span/div/div').text

        except:
            size1 = 'Nothing'
            counter = counter - 1

        # if 'Nothing' in size1:
        #     size2 = 'Nothing'
        #     size3 = 'Nothing'
        #     size4 = 'Nothing'
        #     size5 = 'Nothing'
        #     size6 = 'Nothing'
        # else:
        try:
            counter = counter + 1
            check_if_sold_out = driver.find_element_by_xpath(
                '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[2]/span/div').get_attribute(
                "class")
            if 'radio_soldout' in check_if_sold_out:
                size2 = 'Nothing'

            else:

                size2 = driver.find_element_by_xpath(
                    '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[2]/span/div/div').text
                if size1 in size2 and size1 != 'L' and size2 != 'XL':
                    check_if_sold_out = driver.find_element_by_xpath(
                        '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[3]/span/div').get_attribute(
                        "class")
                    if 'radio_soldout' in check_if_sold_out:
                        size2 = 'Nothing'
                    else:
                        size2 = driver.find_element_by_xpath(
                            '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[3]/span/div/div').text
        except:
            size2 = 'Nothing'
            counter = counter - 1

        try:
            counter = counter + 1
            check_if_sold_out = driver.find_element_by_xpath(
                '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[3]/span/div').get_attribute(
                "class")
            if 'radio_soldout' in check_if_sold_out:
                size3 = 'Nothing'

            else:
                size3 = driver.find_element_by_xpath(
                    '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[3]/span/div/div').text
                if size2 in size3 and size2 != 'L' and size3 != 'XL':
                    check_if_sold_out = driver.find_element_by_xpath(
                        '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[4]/span/div').get_attribute(
                        "class")
                    if 'radio_soldout' in check_if_sold_out:
                        size3 = 'Nothing'

                    else:
                        size3 = driver.find_element_by_xpath(
                            '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[4]/span/div/div').text
        except:
            size3 = 'Nothing'
            counter = counter - 1

        try:
            counter = counter + 1
            check_if_sold_out = driver.find_element_by_xpath(
                '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[4]/span/div').get_attribute(
                "class")
            if 'radio_soldout' in check_if_sold_out:
                size4 = 'Nothing'

            else:
                size4 = driver.find_element_by_xpath(
                    '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[4]/span/div/div').text
                if size3 in size4 and size3 != 'L' and size4 != 'XL':
                    check_if_sold_out = driver.find_element_by_xpath(
                        '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[5]/span/div').get_attribute(
                        "class")
                    if 'radio_soldout' in check_if_sold_out:
                        size4 = 'Nothing'

                    else:
                        size4 = driver.find_element_by_xpath(
                            '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[5]/span/div/div').text
        except:
            counter = counter - 1
            size4 = 'Nothing'

        try:
            counter = counter + 1
            check_if_sold_out = driver.find_element_by_xpath(
                '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[5]/span/div').get_attribute(
                "class")
            if 'radio_soldout' in check_if_sold_out:
                size5 = 'Nothing'

            else:
                size5 = driver.find_element_by_xpath(
                    '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[5]/span/div/div').text
                if size4 in size5 and size4 != 'L' and size5 != 'XL':
                    check_if_sold_out = driver.find_element_by_xpath(
                        '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[6]/span/div').get_attribute(
                        "class")
                    if 'radio_soldout' in check_if_sold_out:
                        size5 = 'Nothing'

                    else:
                        size5 = driver.find_element_by_xpath(
                            '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[6]/span/div/div').text
        except:
            counter = counter - 1
            size5 = 'Nothing'

        try:
            counter = counter + 1
            check_if_sold_out = driver.find_element_by_xpath(
                '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[6]/span/div').get_attribute(
                "class")
            if 'radio_soldout' in check_if_sold_out:
                size6 = 'Nothing'

            else:
                size6 = driver.find_element_by_xpath(
                    '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[6]/span/div/div').text
                if size5 in size6 and size5 != 'L' and size6 != 'XL':
                    check_if_sold_out = driver.find_element_by_xpath(
                        '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[7]/span/div').get_attribute(
                        "class")
                    if 'radio_soldout' in check_if_sold_out:
                        size6 = 'Nothing'

                    else:
                        size6 = driver.find_element_by_xpath(
                            '/html/body/div[1]/div[1]/div/div[1]/div/div[2]/div[2]/div/div[2]/div/div[1]/div[2]/div[7]/span/div/div').text
        except:
            counter = counter - 1
            size6 = 'Nothing'
        counterT = str(counter)

        driver.quit()

        return Product(price=price, size1=size1, size2=size2, size3=size3,
                       size4=size4, size5=size5, size6=size6, counterT=counter)

    def get_raw_price(self, string):
        if '€' in string:
            convert_price = 19.10
        elif "$" in string:
            convert_price = 18.26
        else:
            convert_price = 4.87

        new_str = ''
        for each in string:
            if each in "1234567890.,":
                new_str += each
        if ',' in new_str:
            new_str = new_str.replace(',', '.')
        price = round(float(new_str) * convert_price)
        return price

    @http.route([
        '''/shop''',
        '''/shop/page/<int:page>''',
        '''/shop/category/<model("product.public.category"):category>''',
        '''/shop/category/<model("product.public.category"):category>/page/<int:page>'''
    ], type='http', auth="public", website=True, sitemap=sitemap_shop)
    def shop(self, page=0, category=None, search='', min_price=0.0, max_price=0.0, ppg=False, **post):
        res = super(WebsiteSale_inhernet, self).shop(page=0, category=None, search='', min_price=0.0, max_price=0.0,
                                                     ppg=False, **post)

        add_qty = int(post.get('add_qty', 1))
        try:
            min_price = float(min_price)
        except ValueError:
            min_price = 0
        try:
            max_price = float(max_price)
        except ValueError:
            max_price = 0

        Category = request.env['product.public.category']
        if category:
            category = Category.search([('id', '=', int(category))], limit=1)
            if not category or not category.can_access_from_current_website():
                raise NotFound()
        else:
            category = Category

        if ppg:
            try:
                ppg = int(ppg)
                post['ppg'] = ppg
            except ValueError:
                ppg = False
        if not ppg:
            ppg = request.env['website'].get_current_website().shop_ppg or 20

        ppr = request.env['website'].get_current_website().shop_ppr or 4

        attrib_list = request.httprequest.args.getlist('attrib')
        attrib_values = [[int(x) for x in v.split("-")] for v in attrib_list if v]
        attributes_ids = {v[0] for v in attrib_values}
        attrib_set = {v[1] for v in attrib_values}

        keep = QueryURL('/shop', category=category and int(category), search=search, attrib=attrib_list,
                        min_price=min_price, max_price=max_price, order=post.get('order'))

        pricelist_context, pricelist = self._get_pricelist_context()

        request.context = dict(request.context, pricelist=pricelist.id, partner=request.env.user.partner_id)

        filter_by_price_enabled = request.website.is_view_active('website_sale.filterxs_price')
        if filter_by_price_enabled:
            company_currency = request.website.company_id.currency_id
            conversion_rate = request.env['res.currency']._get_conversion_rate(company_currency, pricelist.currency_id,
                                                                               request.website.company_id,
                                                                               fields.Date.today())
        else:
            conversion_rate = 1

        url = "/shop"
        if search:
            post["search"] = search
        if attrib_list:
            post['attrib'] = attrib_list

        options = {
            'displayDescription': True,
            'displayDetail': True,
            'displayExtraDetail': True,
            'displayExtraLink': True,
            'displayImage': True,
            'allowFuzzy': not post.get('noFuzzy'),
            'category': str(category.id) if category else None,
            'min_price': min_price / conversion_rate,
            'max_price': max_price / conversion_rate,
            'attrib_values': attrib_values,
            'display_currency': pricelist.currency_id,
        }
        # No limit because attributes are obtained from complete product list
        product_count, details, fuzzy_search_term = request.website._search_with_fuzzy("products_only", search,
                                                                                       limit=None,
                                                                                       order=self._get_search_order(
                                                                                           post), options=options)
        # removing the excess pages
        if category:
            # for personal store
            if request.env['product.public.category'].sudo().search([('id', '=', '8')]) in category:
                if 2 == request.env.user.id:
                    RR = request.env['product.template'].sudo().search([])

                    for x in RR:
                        Products_idz = x.product_description
                        print(x.name)
                        if Products_idz:

                            product = self.updatexs(Products_idz)

                            x.sudo().write({'list_price': self.get_raw_price(product.price)})
                            name = x.name

                            Updating_samexs = request.env['product.template'].sudo().search(
                                [('name', '=', name)])
                            # good till here

                            Attribute = request.env['product.attribute'].sudo().search([('name', '=', 'Size')])
                            counter = int(product.counterT)
                            WhichSize: str = None

                            sizezList = [product.size1, product.size2, product.size3, product.size4, product.size5,
                                         product.size6, ]
                            for qqq in sizezList:
                                if 'Nothing' in sizezList:
                                    sizezList.remove('Nothing')

                            sizezList_odoo = sizezList
                            sizezList_odoo.extend(('1', '2', '3', '4', '5', '6'))

                            try:
                                print("First item")
                                for ll in sizezList:

                                    val = request.env['product.attribute.value'].sudo().search([('name', '=', ll,)])

                                    if sizezList_odoo[0] == ll:
                                        size_1 = val
                                        if size_1:
                                            WhichSize = "F1"

                                    if sizezList_odoo[1] == ll:
                                        size_2 = val
                                        if size_2:
                                            WhichSize = "F2"

                                    if sizezList_odoo[2] == ll:
                                        size_3 = val
                                        if size_3:
                                            WhichSize = "F3"

                                    if sizezList_odoo[3] == ll:
                                        size_4 = val
                                        if size_4:
                                            WhichSize = "F4"

                                    if sizezList_odoo[4] == ll:
                                        size_5 = val
                                        if size_5:
                                            WhichSize = "F5"

                                    if sizezList_odoo[5] == ll:
                                        size_6 = val
                                        if size_6:
                                            WhichSize = "F6"


                            except:
                                No_attribute = 0

                            try:
                                if size_1 and "F1" in WhichSize:
                                    ptal = request.env['product.template.attribute.line'].sudo().create({
                                        'attribute_id': Attribute.id if Attribute else False,
                                        'product_tmpl_id': x.id,
                                        'value_ids': [(6, 0, [size_1.id])],
                                    })
                                    x.sudo().write({'attribute_line_ids': [(6, 0, [ptal.id])]})

                            except:
                                pass
                            try:
                                if size_2 and "F2" in WhichSize:
                                    ptal = request.env['product.template.attribute.line'].sudo().create({
                                        'attribute_id': Attribute.id if Attribute else False,
                                        'product_tmpl_id': x.id,
                                        'value_ids': [(6, 0, [size_1.id, size_2.id])],
                                    })
                                    x.sudo().write({'attribute_line_ids': [(6, 0, [ptal.id])]})
                            except:
                                pass
                            try:
                                if size_3 and "F3" in WhichSize:
                                    ptal = request.env['product.template.attribute.line'].sudo().create({
                                        'attribute_id': Attribute.id if Attribute else False,
                                        'product_tmpl_id': x.id,
                                        'value_ids': [(6, 0, [size_1.id, size_2.id, size_3.id])],
                                    })
                                    x.sudo().write({'attribute_line_ids': [(6, 0, [ptal.id])]})
                            except:
                                pass
                            try:
                                if size_4 and "F4" in WhichSize:
                                    ptal = request.env['product.template.attribute.line'].sudo().create({
                                        'attribute_id': Attribute.id if Attribute else False,
                                        'product_tmpl_id': x.id,
                                        'value_ids': [(6, 0, [size_1.id, size_2.id, size_3.id, size_4.id])],
                                    })
                                    x.sudo().write({'attribute_line_ids': [(6, 0, [ptal.id])]})

                            except:
                                pass
                            try:
                                if size_5 and "F5" in WhichSize:
                                    ptal = request.env['product.template.attribute.line'].sudo().create({
                                        'attribute_id': Attribute.id if Attribute else False,
                                        'product_tmpl_id': x.id,
                                        'value_ids': [
                                            (6, 0, [size_1.id, size_2.id, size_3.id, size_4.id, size_5.id])],
                                    })
                                    x.sudo().write({'attribute_line_ids': [(6, 0, [ptal.id])]})
                            except:
                                pass
                            try:

                                if size_6 and "F6" in WhichSize:
                                    ptal = request.env['product.template.attribute.line'].sudo().create({
                                        'attribute_id': Attribute.id if Attribute else False,
                                        'product_tmpl_id': x.id,
                                        'value_ids': [
                                            (6, 0,
                                             [size_1.id, size_2.id, size_3.id, size_4.id, size_5.id, size_6.id])],
                                    })
                                    x.sudo().write({'attribute_line_ids': [(6, 0, [ptal.id])]})
                            except:
                                pass

                            for xy in Updating_samexs:

                                xy.sudo().write({'list_price': self.get_raw_price(product.price)})

                                Attribute = request.env['product.attribute'].sudo().search([('name', '=', 'Size')])
                                counter = int(product.counterT)
                                WhichSize: str = None
                                sizezList = [product.size1, product.size2, product.size3, product.size4, product.size5,
                                             product.size6, ]
                                for qqq in sizezList:
                                    if 'Nothing' in sizezList:
                                        sizezList.remove('Nothing')

                                sizezList_odoo = sizezList
                                sizezList_odoo.extend(('1', '2', '3', '4', '5', '6'))

                                try:
                                    for ll in sizezList:

                                        val = request.env['product.attribute.value'].sudo().search([('name', '=', ll,)])

                                        if sizezList_odoo[0] == ll:
                                            size_1 = val
                                            if size_1:
                                                WhichSize = "F1"

                                        if sizezList_odoo[1] == ll:
                                            size_2 = val
                                            if size_2:
                                                WhichSize = "F2"

                                        if sizezList_odoo[2] == ll:
                                            size_3 = val
                                            if size_3:
                                                WhichSize = "F3"

                                        if sizezList_odoo[3] == ll:
                                            size_4 = val
                                            if size_4:
                                                WhichSize = "F4"

                                        if sizezList_odoo[4] == ll:
                                            size_5 = val
                                            if size_5:
                                                WhichSize = "F5"

                                        if sizezList_odoo[5] == ll:
                                            size_6 = val
                                            if size_6:
                                                WhichSize = "F6"


                                except:
                                    No_attribute = 0
                                print(xy)
                                print(Attribute)
                                print(size_1, size_2, size_3, size_4)
                                print(WhichSize)
                                try:
                                    if size_1 and "F1" in WhichSize:
                                        ptal = request.env['product.template.attribute.line'].sudo().create({
                                            'attribute_id': Attribute.id if Attribute else False,
                                            'product_tmpl_id': xy.id,
                                            'value_ids': [(6, 0, [size_1.id])],
                                        })
                                        xy.sudo().write({'attribute_line_ids': [(6, 0, [ptal.id])]})

                                except:
                                    pass
                                try:
                                    if size_2 and "F2" in WhichSize:
                                        ptal = request.env['product.template.attribute.line'].sudo().create({
                                            'attribute_id': Attribute.id if Attribute else False,
                                            'product_tmpl_id': xy.id,
                                            'value_ids': [(6, 0, [size_1.id, size_2.id])],
                                        })
                                        xy.sudo().write({'attribute_line_ids': [(6, 0, [ptal.id])]})
                                except:
                                    pass
                                try:
                                    if size_3 and "F3" in WhichSize:
                                        ptal = request.env['product.template.attribute.line'].sudo().create({
                                            'attribute_id': Attribute.id if Attribute else False,
                                            'product_tmpl_id': xy.id,
                                            'value_ids': [(6, 0, [size_1.id, size_2.id, size_3.id])],
                                        })
                                        xy.sudo().write({'attribute_line_ids': [(6, 0, [ptal.id])]})
                                except:
                                    pass
                                try:
                                    if size_4 and "F4" in WhichSize:
                                        ptal = request.env['product.template.attribute.line'].sudo().create({
                                            'attribute_id': Attribute.id if Attribute else False,
                                            'product_tmpl_id': xy.id,
                                            'value_ids': [(6, 0, [size_1.id, size_2.id, size_3.id, size_4.id])],
                                        })
                                        xy.sudo().write({'attribute_line_ids': [(6, 0, [ptal.id])]})

                                except:
                                    pass
                                try:
                                    if size_5 and "F5" in WhichSize:
                                        ptal = request.env['product.template.attribute.line'].sudo().create({
                                            'attribute_id': Attribute.id if Attribute else False,
                                            'product_tmpl_id': xy.id,
                                            'value_ids': [
                                                (6, 0, [size_1.id, size_2.id, size_3.id, size_4.id, size_5.id])],
                                        })
                                        xy.sudo().write({'attribute_line_ids': [(6, 0, [ptal.id])]})
                                except:
                                    pass
                                try:

                                    if size_6 and "F6" in WhichSize:
                                        ptal = request.env['product.template.attribute.line'].sudo().create({
                                            'attribute_id': Attribute.id if Attribute else False,
                                            'product_tmpl_id': xy.id,
                                            'value_ids': [
                                                (6, 0,
                                                 [size_1.id, size_2.id, size_3.id, size_4.id, size_5.id, size_6.id])],
                                        })
                                        xy.sudo().write({'attribute_line_ids': [(6, 0, [ptal.id])]})
                                except:
                                    pass
                            break
                            # break here since it needs to update the whole list first we put a break
                            # for demo

                    product_count = len(RR)

                else:
                    RR = request.env['product.template'].sudo().search(
                        [('responsible_id', '!=', request.env.user.id,)])
                    product_count = product_count - len(RR)






        else:
            # removing redundancy from view
            RR = request.env['product.template'].sudo().search(
                [('public_categ_ids', '!=', 8,)])
            product_count = product_count - len(RR)
            # youssef display the certain products here

        searchx = details[0].get('results', request.env['product.template']).with_context(bin_size=True)

        filter_by_price_enabled = request.website.is_view_active('website_sale.filterxs_price')
        if filter_by_price_enabled:
            # TODO Find an alternative way to obtain the domain through the search metadata.
            Product = request.env['product.template'].with_context(bin_size=True)
            domain = self._get_search_domain(search, category, attrib_values)

            # This is ~4 times more efficient than a search for the cheapest and most expensive products
            from_clause, where_clause, where_params = Product._where_calc(domain).get_sql()
            query = f"""
                SELECT COALESCE(MIN(list_price), 0) * {conversion_rate}, COALESCE(MAX(list_price), 0) * {conversion_rate}
                  FROM {from_clause}
                 WHERE {where_clause}
            """
            request.env.cr.execute(query, where_params)
            available_min_price, available_max_price = request.env.cr.fetchone()

            if min_price or max_price:
                # The if/else condition in the min_price / max_price value assignment
                # tackles the case where we switch to a list of products with different
                # available min / max prices than the ones set in the previous page.
                # In order to have logical results and not yield empty product lists, the
                # price filter is set to their respective available prices when the specified
                # min exceeds the max, and / or the specified max is lower than the available min.
                if min_price:
                    min_price = min_price if min_price <= available_max_price else available_min_price
                    post['min_price'] = min_price
                if max_price:
                    max_price = max_price if max_price >= available_min_price else available_max_price
                    post['max_price'] = max_price

        website_domain = request.website.website_domain()
        categs_domain = [('parent_id', '=', False)] + website_domain
        if search:
            search_categories = Category.search(
                [('product_tmpl_ids', 'in', searchx.ids)] + website_domain).parents_and_self
            categs_domain.append(('id', 'in', search_categories.ids))

        else:
            search_categories = Category
        categs = Category.search(categs_domain)

        if category:
            url = "/shop/category/%s" % slug(category)

        pager = request.website.pager(url=url, total=product_count, page=page, step=ppg, scope=7, url_args=post)
        offset = pager['offset']

        # youssef category selection
        if category:
            # for personal store
            if request.env['product.public.category'].sudo().search([('id', '=', '8')]) in category:
                if 2 == request.env.user.id:
                    products = request.env['product.template'].sudo().search([])[offset:offset + ppg]




                else:
                    products = request.env['product.template'].sudo().search(
                        [('responsible_id', '=', request.env.user.id,
                          )],
                        order='id desc')[offset:offset + ppg]









            else:
                # for fawary category just make the framework go with its flow
                products = searchx[offset:offset + ppg]

        else:
            # removing redundancy from view
            RR = request.env['product.template'].sudo().search(
                [('public_categ_ids', '!=', 8,)])
            searchx = searchx - RR
            # youssef display the certain products here
            products = searchx[offset:offset + ppg]

        ProductAttribute = request.env['product.attribute']
        if products:
            # get all products without limit
            attributes = ProductAttribute.search([
                ('product_tmpl_ids', 'in', searchx.ids),
                ('visibility', '=', 'visible'),
            ])
        else:
            attributes = ProductAttribute.browse(attributes_ids)

        layout_mode = request.session.get('website_sale_shop_layout_mode')
        if not layout_mode:
            if request.website.viewref('website_sale.products_list_view').active:
                layout_mode = 'list'
            else:
                layout_mode = 'grid'
        # x = products
        #
        # print(x)

        values = {
            'search': fuzzy_search_term or search,
            'original_search': fuzzy_search_term and search,
            'category': category,
            'attrib_values': attrib_values,
            'attrib_set': attrib_set,
            'pager': pager,
            'pricelist': pricelist,
            'add_qty': add_qty,
            'products': products,
            'search_count': product_count,  # common for all searchbox
            'bins': TableCompute().process(products, ppg, ppr),
            'ppg': ppg,
            'ppr': ppr,
            'categories': categs,
            'attributes': attributes,
            'keep': keep,
            'search_categories_ids': search_categories.ids,
            'layout_mode': layout_mode,
        }
        if filter_by_price_enabled:
            values['min_price'] = min_price or available_min_price
            values['max_price'] = max_price or available_max_price
            values['available_min_price'] = tools.float_round(available_min_price, 2)
            values['available_max_price'] = tools.float_round(available_max_price, 2)
        if category:
            values['main_object'] = category
        return request.render("website_sale.products", values)

        return res
